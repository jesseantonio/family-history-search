"""Reconhecimento de datas em português — completas, parciais e relativas.

Objetivo (Fase 3): extrair a data do EVENTO mencionado no trecho, que é
frequentemente diferente da data de publicação da edição (ex: uma notícia
publicada em novembro pode descrever algo que aconteceu "na manhã de
segunda-feira última"). Nada é inventado: dia e mês só aparecem quando
estão escritos no texto ou quando decorrem diretamente da data de
publicação ("hontem" num jornal de 3 de janeiro é 2 de janeiro). Toda data
calculada assim é marcada com `resolved_from="publicacao"`.

Acentos: o texto é comparado sem acento, e o caractere "�" (acento perdido
no OCR do acervo) é aceito no lugar de qualquer letra acentuada das
palavras-chave ("mar�o", "ter�a-feira", "�ltima").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, timedelta

MONTHS: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTHS_WRITTEN = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                   "agosto", "setembro", "outubro", "novembro", "dezembro"]

# grafia com acento: o acento vira "letra base ou �" no padrão; valor = date.weekday()
_WEEKDAYS = {
    "segunda-feira": 0, "terça-feira": 1, "quarta-feira": 2, "quinta-feira": 3, "sexta-feira": 4,
    "segunda": 0, "terça": 1, "quarta": 2, "quinta": 3, "sexta": 4,
    "sábado": 5, "sabbado": 5, "domingo": 6,
}
_RELATIVE_DAY_OFFSETS = {
    "hoje": 0, "hontem": -1, "ontem": -1, "anteontem": -2, "ante-hontem": -2, "antehontem": -2,
    "ante-ontem": -2, "amanhã": 1,
}

_REPLACEMENT_CHAR = "�"


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _fold(text: str) -> str:
    """Remove acentos caractere a caractere, mantendo o mesmo comprimento (os offsets
    das menções continuam valendo no texto original)."""
    out = []
    for ch in text:
        base = _strip_accents(ch)
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


def _word_pattern(word: str) -> str:
    """"março" -> "mar[c�]o": letras acentuadas aceitam a base ou o "�" do OCR."""
    parts = []
    for ch in word:
        base = _strip_accents(ch)
        if base != ch:
            parts.append(f"[{re.escape(base)}{_REPLACEMENT_CHAR}]")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _alternation(words) -> str:
    return "|".join(_word_pattern(w) for w in sorted(words, key=len, reverse=True))


_B = r"(?<!\w)"   # fronteira que funciona também quando a palavra começa/termina com "�"
_E = r"(?!\w)"
_MONTH_ALT = _alternation(_MONTHS_WRITTEN)
_WEEKDAY_ALT = _alternation(_WEEKDAYS)
_RELATIVE_ALT = _alternation(_RELATIVE_DAY_OFFSETS)
_DAY = r"(\d{1,2})(?:\s*[.]?\s*[oº°])?"   # "3", "1º", "1.º", "1o"

_FULL_DATE_RE = re.compile(
    rf"{_B}{_DAY}\s+de\s+({_MONTH_ALT})(?:\s+de\s+(\d{{4}}))?{_E}", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(rf"{_B}({_MONTH_ALT})\s+de\s+(\d{{4}}){_E}", re.IGNORECASE)
_RELATIVE_WEEKDAY_RE = re.compile(
    rf"{_B}({_WEEKDAY_ALT})\s+({_alternation(['última', 'último', 'passada', 'passado', 'próxima', 'próximo'])}){_E}",
    re.IGNORECASE)
_RELATIVE_WORD_RE = re.compile(rf"{_B}({_RELATIVE_ALT}){_E}", re.IGNORECASE)
_CURRENT_MONTH_RE = re.compile(rf"{_B}(?:(?:dia\s+)?{_DAY}\s+)?do\s+corrente(?:\s+m{_word_pattern('ê')}[sz])?{_E}",
                               re.IGNORECASE)
_LAST_MONTH_RE = re.compile(rf"{_B}(?:(?:dia\s+)?{_DAY}\s+)?do\s+m{_word_pattern('ê')}[sz]\s+passado{_E}",
                            re.IGNORECASE)


@dataclass(frozen=True)
class DateMention:
    raw_text: str
    start_offset: int
    end_offset: int
    precision: str  # "dia" | "mes" | "relativa" (relativa = não deu para calcular)
    is_relative: bool
    year: int | None
    month: int | None
    day: int | None
    # "publicacao" quando ano/mês/dia foram calculados a partir da data de publicação
    # (data relativa, ou data sem ano); None quando estão escritos no texto
    resolved_from: str | None = None

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
    if key in MONTHS:
        return MONTHS[key]
    if _REPLACEMENT_CHAR in key:
        rx = re.compile(re.escape(key).replace(_REPLACEMENT_CHAR, "."))
        found = [n for m, n in MONTHS.items() if rx.fullmatch(m)]
        return found[0] if len(found) == 1 else None
    return None


def _weekday_number(name: str) -> int | None:
    folded = _strip_accents(name).lower()
    for w, i in _WEEKDAYS.items():
        if re.fullmatch(_word_pattern(w), folded, re.IGNORECASE):
            return i
    return None


def _relative_offset(word: str) -> int | None:
    folded = _strip_accents(word).lower()
    for w, off in _RELATIVE_DAY_OFFSETS.items():
        if re.fullmatch(_word_pattern(w), folded, re.IGNORECASE):
            return off
    return None


def _valid_day(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def _non_overlapping(existing: list[tuple[int, int]], start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e in existing)


def _previous_month(d: date) -> tuple[int, int]:
    return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)


def _resolve(m: DateMention, folded_raw: str, pub: date | None, ref_year: int | None) -> DateMention:
    """Calcula ano/mês/dia de uma menção relativa (ou sem ano) a partir da publicação."""
    if not m.is_relative:
        if m.year is not None:
            return m
        # "3 de novembro" sem ano: ano da publicação; se cair mais de 6 meses depois
        # dela, é do ano anterior (ex.: jornal de janeiro citando "28 de dezembro")
        if pub is not None:
            year = pub.year
            if _valid_day(year, m.month, m.day) and date(year, m.month, m.day) - pub > timedelta(days=183):
                year -= 1
        elif ref_year is not None:
            year = ref_year
        else:
            return m
        if not _valid_day(year, m.month, m.day):
            return m
        return replace(m, year=year, resolved_from="publicacao")

    if pub is None:
        return m
    rel_word = _RELATIVE_WORD_RE.fullmatch(folded_raw)
    if rel_word:
        offset = _relative_offset(rel_word.group(1))
        if offset is None:
            return m
        d = pub + timedelta(days=offset)
        return replace(m, precision="dia", year=d.year, month=d.month, day=d.day, resolved_from="publicacao")
    wk = _RELATIVE_WEEKDAY_RE.fullmatch(folded_raw)
    if wk:
        target = _weekday_number(wk.group(1))
        if target is None:
            return m
        if wk.group(2).lower().startswith("pr"):          # próximo/próxima
            delta = (target - pub.weekday()) % 7 or 7
            d = pub + timedelta(days=delta)
        else:                                             # última/passado
            delta = (pub.weekday() - target) % 7 or 7
            d = pub - timedelta(days=delta)
        return replace(m, precision="dia", year=d.year, month=d.month, day=d.day, resolved_from="publicacao")
    for rx, month_of in ((_CURRENT_MONTH_RE, lambda p: (p.year, p.month)), (_LAST_MONTH_RE, _previous_month)):
        mm = rx.fullmatch(folded_raw)
        if mm:
            year, month = month_of(pub)
            day = int(mm.group(1)) if mm.group(1) else None
            if day is not None and _valid_day(year, month, day):
                return replace(m, precision="dia", year=year, month=month, day=day, resolved_from="publicacao")
            return replace(m, precision="mes", year=year, month=month, resolved_from="publicacao")
    return m


def extract_dates(text: str, publication_date: str | date | None = None,
                  reference_year: int | None = None) -> list[DateMention]:
    """Extrai todas as menções de data do texto. Datas mais específicas têm prioridade
    sobre menções mais genéricas que as sobrepõem (ex: "3 de novembro de 1909" não gera
    também uma menção solta de "novembro de 1909").

    Com `publication_date` (ISO "AAAA-MM-DD" ou `date`), datas relativas ("hontem",
    "sabbado passado", "dia 5 do corrente") e datas sem ano são resolvidas a partir dela.
    Sem ela, `reference_year` (ex.: o ano da edição) completa só as datas sem ano.
    """
    pub = date.fromisoformat(publication_date) if isinstance(publication_date, str) else publication_date
    folded = _fold(text)
    mentions: list[DateMention] = []
    consumed: list[tuple[int, int]] = []

    def add(start: int, end: int, **kw) -> None:
        mention = DateMention(raw_text=text[start:end], start_offset=start, end_offset=end, **kw)
        mentions.append(_resolve(mention, folded[start:end], pub, reference_year))
        consumed.append((start, end))

    for match in _FULL_DATE_RE.finditer(folded):
        month = _month_number(match.group(2))
        day = int(match.group(1))
        year = int(match.group(3)) if match.group(3) else None
        if month is None or not 1 <= day <= 31:
            continue
        add(*match.span(), precision="dia", is_relative=False, year=year, month=month, day=day)

    for match in _MONTH_YEAR_RE.finditer(folded):
        start, end = match.span()
        month = _month_number(match.group(1))
        if month is None or not _non_overlapping(consumed, start, end):
            continue
        add(start, end, precision="mes", is_relative=False, year=int(match.group(2)), month=month, day=None)

    for pattern in (_RELATIVE_WEEKDAY_RE, _CURRENT_MONTH_RE, _LAST_MONTH_RE, _RELATIVE_WORD_RE):
        for match in pattern.finditer(folded):
            start, end = match.span()
            if not _non_overlapping(consumed, start, end):
                continue
            add(start, end, precision="relativa", is_relative=True, year=None, month=None, day=None)

    mentions.sort(key=lambda m: m.start_offset)
    return mentions
