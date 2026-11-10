"""Classificação do tipo de evento (nascimento, casamento, óbito, outro).

Baseado em palavras-chave, não em modelo treinado — não existe amostra
anotada (Fase 0) pra treinar ou validar um classificador de ML, então
regras explícitas são a opção honesta: o motivo da classificação é sempre
visível (qual palavra bateu), o que facilita a correção manual que o
README exige.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nascimento": ("nasceu", "nascimento", "nascida", "nascido", "deu a luz", "recem-nascid", "natalicio"),
    "casamento": (
        "casou-se", "casou", "casamento", "consorciou-se", "consorciou",
        "matrimonio", "esposou", "desposou", "nubente", "contraiu matrimonio",
    ),
    "obito": (
        "faleceu", "falecimento", "obito", "morreu", "morte de", "sepultamento",
        "funeral", "pesames", "condolencias", "necrologio", "extincto",
    ),
}


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class EventClassification:
    event_type: str  # "nascimento" | "casamento" | "obito" | "outro"
    evidence: str | None  # a palavra/expressão que motivou a classificação
    confidence: float


# Em empate, vence o evento mais à esquerda: um obituário costuma citar o nascimento
# ("faleceu ... nascido em 1850"), e uma nota de casamento raramente cita um óbito.
_TIE_PRIORITY = ("obito", "casamento", "nascimento")

_KEYWORD_RES: dict[str, list[tuple[str, re.Pattern]]] = {
    event_type: [(kw, re.compile(r"(?<!\w)" + re.escape(kw))) for kw in keywords]
    for event_type, keywords in EVENT_KEYWORDS.items()
}


def classify_event(text: str) -> EventClassification:
    """Conta as ocorrências das palavras-chave de cada tipo (só no início de palavra,
    para "casou" não bater dentro de outra palavra) e escolhe o tipo com mais
    ocorrências; empate é decidido por `_TIE_PRIORITY`. A evidência é a palavra mais
    frequente do tipo escolhido. Quando mais de um tipo aparece, a confiança cai.
    Quando nenhuma bate, retorna "outro" com confiança baixa — não é uma
    classificação positiva, é a ausência de sinal.
    """
    normalized = _strip_accents(text)
    counts: dict[str, dict[str, int]] = {}
    for event_type, patterns in _KEYWORD_RES.items():
        # "casou-se" também contém "casou": cada trecho do texto conta uma vez só,
        # atribuído à palavra-chave mais longa que o cobre
        spans = sorted(((m.start(), m.end(), kw) for kw, rx in patterns for m in rx.finditer(normalized)),
                       key=lambda s: s[0] - s[1])
        taken: list[tuple[int, int]] = []
        found: dict[str, int] = {}
        for start, end, kw in spans:
            if all(end <= s or start >= e for s, e in taken):
                taken.append((start, end))
                found[kw] = found.get(kw, 0) + 1
        if found:
            counts[event_type] = found
    if not counts:
        return EventClassification(event_type="outro", evidence=None, confidence=0.3)
    best = max(counts, key=lambda t: (sum(counts[t].values()), -_TIE_PRIORITY.index(t)))
    evidence = max(counts[best], key=counts[best].get)
    confidence = 0.7 if len(counts) == 1 else 0.5
    return EventClassification(event_type=best, evidence=evidence, confidence=confidence)
