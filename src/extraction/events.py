"""Classificação do tipo de evento (nascimento, casamento, óbito, outro).

Baseado em palavras-chave, não em modelo treinado — não existe amostra
anotada (Fase 0) pra treinar ou validar um classificador de ML, então
regras explícitas são a opção honesta: o motivo da classificação é sempre
visível (qual palavra bateu), o que facilita a correção manual que o
README exige.
"""

from __future__ import annotations

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


def classify_event(text: str) -> EventClassification:
    """Classifica o tipo de evento pela primeira palavra-chave encontrada.
    Quando nenhuma bate, retorna "outro" com confiança baixa — não é uma
    classificação positiva, é a ausência de sinal.
    """
    normalized = _strip_accents(text)
    for event_type, keywords in EVENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                return EventClassification(event_type=event_type, evidence=keyword, confidence=0.7)
    return EventClassification(event_type="outro", evidence=None, confidence=0.3)
