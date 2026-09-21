"""Reconhecimento de datas em português — completas, parciais e relativas.

Objetivo (Fase 3): extrair a data do EVENTO mencionado no trecho, que é
frequentemente diferente da data de publicação da edição (ex: uma notícia
publicada em novembro pode descrever algo que aconteceu "na manhã de
segunda-feira última"). Este módulo só reconhece e classifica menções de
data no texto — não inventa dia ou mês que não estejam escritos. Resolver
uma data relativa numa data absoluta (quando possível) é responsabilidade
de quem chama, que precisa saber a data de publicação da edição.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MONTHS: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

_WEEKDAYS = [
    "domingo", "segunda-feira", "terca-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sabado",
]

_RELATIVE_WORDS = [
    "hoje", "ontem", "hontem", "anteontem", "ante-hontem", "antehontem",
    "amanha", "hontem a noite", "ontem a noite",
]


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _build_month_pattern() -> str:
    # aceita a grafia sem acento também, já que o OCR do acervo perde acentos
    names = set()
    for name in MONTHS:
        names.add(name)
        names.add(_strip_accents(name))
    return "|".join(sorted(names, key=len, reverse=True))


_MONTH_ALTERNATION = _build_month_pattern()
_WEEKDAY_ALTERNATION = "|".join(_strip_accents(w) for w in _WEEKDAYS)
_RELATIVE_ALTERNATION = "|".join(re.escape(_strip_accents(w)) for w in _RELATIVE_WORDS)

_FULL_DATE_RE = re.compile(
    rf"\b(\d{{1,2}})\s+de\s+({_MONTH_ALTERNATION})(?:\s+de\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(
    rf"\b({_MONTH_ALTERNATION})\s+de\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_RELATIVE_WEEKDAY_RE = re.compile(
    rf"\b({_WEEKDAY_ALTERNATION})\s+(ultima|passad[ao]|proxim[ao])\b",
    re.IGNORECASE,
)
_RELATIVE_WORD_RE = re.compile(rf"\b({_RELATIVE_ALTERNATION})\b", re.IGNORECASE)
_RELATIVE_CURRENT_MONTH_RE = re.compile(r"\bdo\s+corrente\b", re.IGNORECASE)
_RELATIVE_LAST_MONTH_RE = re.compile(r"\bdo\s+m[eê]s?\s+passado\b", re.IGNORECASE)


@dataclass(frozen=True)
class DateMention:
    raw_text: str
    start_offset: int
    end_offset: int
    precision: str  # "dia" | "mes" | "relativa"
    is_relative: bool
    year: int | None
    month: int | None
    day: int | None

    @property
    def date_value(self) -> str | None:
        """ISO parcial (YYYY, YYYY-MM ou YYYY-MM-DD); None quando não há ano."""
        if self.year is None:
            return None
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


def _month_number(name: str) -> int | None:
    key = _strip_accents(name).lower()
    return MONTHS.get(key)


def _non_overlapping(existing: list[tuple[int, int]], start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e in existing)


def extract_dates(text: str) -> list[DateMention]:
    """Extrai todas as menções de data do texto, sem inventar dia/mês que
    não estejam escritos. Datas mais específicas têm prioridade sobre
    menções mais genéricas que as sobrepõem (ex: "3 de novembro de 1909"
    não também gera uma menção solta de "novembro de 1909").
    """
    mentions: list[DateMention] = []
    consumed: list[tuple[int, int]] = []

    for match in _FULL_DATE_RE.finditer(text):
        start, end = match.span()
        month = _month_number(match.group(2))
        if month is None:
            continue
        year = int(match.group(3)) if match.group(3) else None
        mentions.append(DateMention(
            raw_text=match.group(0), start_offset=start, end_offset=end,
            precision="dia", is_relative=False,
            year=year, month=month, day=int(match.group(1)),
        ))
        consumed.append((start, end))

    for match in _MONTH_YEAR_RE.finditer(text):
        start, end = match.span()
        if not _non_overlapping(consumed, start, end):
            continue
        month = _month_number(match.group(1))
        if month is None:
            continue
        mentions.append(DateMention(
            raw_text=match.group(0), start_offset=start, end_offset=end,
            precision="mes", is_relative=False,
            year=int(match.group(2)), month=month, day=None,
        ))
        consumed.append((start, end))

    for pattern in (_RELATIVE_WEEKDAY_RE, _RELATIVE_CURRENT_MONTH_RE, _RELATIVE_LAST_MONTH_RE, _RELATIVE_WORD_RE):
        for match in pattern.finditer(text):
            start, end = match.span()
            if not _non_overlapping(consumed, start, end):
                continue
            mentions.append(DateMention(
                raw_text=match.group(0), start_offset=start, end_offset=end,
                precision="relativa", is_relative=True,
                year=None, month=None, day=None,
            ))
            consumed.append((start, end))

    mentions.sort(key=lambda m: m.start_offset)
    return mentions
