"""Características de similaridade entre pares de menções de pessoa (Fase 5).

O objeto de comparação é a MENÇÃO — um nome específico numa ocorrência
específica — não a notícia inteira. Duas menções com o mesmo sobrenome
podem ser pessoas completamente diferentes da mesma família (é uma
família grande — o objetivo aqui não é provar que são a mesma pessoa,
é sugerir, com evidência visível, quais menções PODEM ser. Nomes iguais
não garantem identidade, e um score alto não comprova parentesco.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

MAX_YEAR_SPAN = 60  # anos; distância >= isso => compatibilidade temporal ~0


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# tratamentos que antecedem o nome e não fazem parte dele ("d. Ana Kraus" é "Ana Kraus")
_TITLES = {"d", "da", "dr", "dra", "sr", "sra", "sr3", "srta", "dna", "exmo", "exma", "snr", "snra",
           "prof", "profa", "cel", "cap", "ten", "maj", "revmo", "pe", "viuva", "viuvo"}


def normalize_name(name: str) -> str:
    tokens = _strip_accents(name).replace(",", " ").split()
    while len(tokens) > 1 and tokens[0].rstrip(".") in _TITLES:
        tokens.pop(0)
    return " ".join(tokens)


@dataclass(frozen=True)
class PersonMentionRecord:
    mention_id: str
    occurrence_id: str
    name_raw: str
    name_normalized: str
    source: str  # "ancora" (person_anchor da ocorrência) | "relacao" (sujeito/alvo de uma relação)
    year: int | None
    year_precision: str | None
    places: frozenset[str]
    associated_people: frozenset[str]
    periodical: str
    context_excerpt: str
    source_url: str


def _resolve_year(occurrence: dict) -> tuple[int | None, str | None]:
    dates = occurrence.get("dates") or []
    with_year = [d for d in dates if d.get("year")]
    if with_year:
        best = min(with_year, key=lambda d: {"dia": 0, "mes": 1}.get(d["precision"], 2))
        return best["year"], best["precision"]
    year = occurrence.get("year")
    return (year, "publicacao") if year else (None, None)


def build_mentions(occurrences: list[dict], surname: str) -> list[PersonMentionRecord]:
    """Extrai as menções de pessoa relevantes de cada ocorrência: o nome
    ancorado no sobrenome (`person_anchor`) e qualquer sujeito/alvo de
    relação que também contenha o sobrenome (ex: um pai/mãe/cônjuge que
    também se chama "Effting" é uma menção própria, não só um detalhe
    da menção principal).
    """
    needle = _strip_accents(surname)
    mentions: list[PersonMentionRecord] = []

    for occ in occurrences:
        year, precision = _resolve_year(occ)
        places = frozenset(normalize_name(p["name_raw"]) for p in occ.get("places_mentioned", []))

        associated_all = {normalize_name(p["name_raw"]) for p in occ.get("people_mentioned", [])}
        for rel in occ.get("relations", []):
            if rel.get("subject_raw"):
                associated_all.add(normalize_name(rel["subject_raw"]))
            associated_all.add(normalize_name(rel["target_raw"]))

        candidates: list[tuple[str, str]] = []
        anchor = occ.get("person_anchor")
        if anchor:
            candidates.append((anchor["name_raw"], "ancora"))
        for rel in occ.get("relations", []):
            for raw in (rel.get("subject_raw"), rel.get("target_raw")):
                if raw and needle in _strip_accents(raw):
                    candidates.append((raw, "relacao"))

        seen = set()
        for name_raw, source in candidates:
            norm = normalize_name(name_raw)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            mentions.append(PersonMentionRecord(
                mention_id=f"{occ['id']}::{len(mentions)}",
                occurrence_id=occ["id"], name_raw=name_raw, name_normalized=norm,
                source=source, year=year, year_precision=precision,
                places=places, associated_people=frozenset(associated_all - {norm}),
                periodical=occ.get("periodical", ""), context_excerpt=occ["context_excerpt"],
                source_url=occ["source_url"],
            ))
    return mentions


@dataclass(frozen=True)
class ScoreBreakdown:
    name_similarity: float
    people_in_common: float
    place_proximity: float
    temporal_compatibility: float
    address_overlap: float
    total: float


def name_similarity(a: PersonMentionRecord, b: PersonMentionRecord) -> float:
    return fuzz.token_sort_ratio(a.name_normalized, b.name_normalized) / 100.0


def _jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    union = len(set_a | set_b)
    return len(set_a & set_b) / union if union else 0.0


def people_in_common(a: PersonMentionRecord, b: PersonMentionRecord) -> float:
    return _jaccard(a.associated_people, b.associated_people)


def place_proximity(a: PersonMentionRecord, b: PersonMentionRecord) -> float:
    return _jaccard(a.places, b.places)


def temporal_compatibility(a: PersonMentionRecord, b: PersonMentionRecord) -> float:
    if a.year is None or b.year is None:
        return 0.5  # incerto — não favorece nem penaliza
    distance = abs(a.year - b.year)
    return max(0.0, 1.0 - distance / MAX_YEAR_SPAN)


def address_or_profession_overlap(a: PersonMentionRecord, b: PersonMentionRecord) -> float:
    """Sempre 0: a Fase 3 não extrai profissão, e endereço só existiria via
    relação `residente_em` (já coberta em `place_proximity`). Mantido como
    campo explícito no score — igual ao README pede — em vez de omitido
    silenciosamente, mas sem fingir um sinal que não existe.
    """
    return 0.0


# Pesos de cada característica no score total. Somam 1, então o score vai de 0 a 1 de
# verdade. Endereço/profissão fica com peso 0 enquanto não houver extração desse sinal
# (antes tinha 0,10 e nunca pontuava, o que limitava o score a 0,9).
WEIGHTS = {
    "name_similarity": 0.35 / 0.9,
    "people_in_common": 0.20 / 0.9,
    "place_proximity": 0.20 / 0.9,
    "temporal_compatibility": 0.15 / 0.9,
    "address_overlap": 0.0,
}


def compute_score(a: PersonMentionRecord, b: PersonMentionRecord) -> ScoreBreakdown:
    sim = name_similarity(a, b)
    people = people_in_common(a, b)
    place = place_proximity(a, b)
    temporal = temporal_compatibility(a, b)
    address = address_or_profession_overlap(a, b)
    total = (WEIGHTS["name_similarity"] * sim + WEIGHTS["people_in_common"] * people
             + WEIGHTS["place_proximity"] * place + WEIGHTS["temporal_compatibility"] * temporal
             + WEIGHTS["address_overlap"] * address)
    return ScoreBreakdown(
        name_similarity=sim, people_in_common=people, place_proximity=place,
        temporal_compatibility=temporal, address_overlap=address, total=total,
    )
