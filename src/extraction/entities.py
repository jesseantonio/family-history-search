"""Extração de pessoas e lugares — combina spaCy (NER genérico) com regras
específicas para o nome ancorado no sobrenome buscado.

O NER genérico do spaCy (`pt_core_news_sm`) erra sistematicamente nos
nomes que mais importam pra este projeto: testado no corpus real, "Patrícia
Uliano Effting" saiu classificado como ORG, não PER — provavelmente por
causa do caractere de substituição "�" (acento perdido no OCR) e
abreviações incomuns ("Sr3", "E.A."). Por isso, o nome que contém o
sobrenome buscado é extraído por uma regra dedicada (`find_surname_person`),
não pelo NER — e tende a ser mais confiável exatamente onde mais importa.
O NER genérico ainda é usado para as OUTRAS pessoas e lugares mencionados
no trecho, com confiança mais baixa por causa da qualidade observada.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

_TITLES = ("sr", "sra", "sr3", "srta", "d", "dr", "dra", "viuva", "viuvo")
_CONNECTORS = {"de", "da", "do", "das", "dos", "e"}


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _looks_like_name_token(token: str) -> bool:
    bare = token.strip(".,;:()").rstrip("3")  # "Sr3" costuma ser OCR de "Srª"
    if not bare:
        return False
    if bare.lower() in _CONNECTORS:
        return True
    if bare.lower().rstrip(".") in _TITLES:
        return True
    return bare[0].isupper() or bare.isupper()


def _ends_list_item(token: str) -> bool:
    """Vírgula/ponto-e-vírgula no fim do token normalmente separa uma
    pessoa da próxima numa lista ("Fulano, Beltrano e Cicrano") — não deve
    ser atravessado ao expandir o nome, senão junta pessoas diferentes.
    """
    return token.rstrip().endswith((",", ";"))


@dataclass(frozen=True)
class PersonMention:
    name_raw: str
    start_offset: int
    end_offset: int
    source: str  # "ancora" (regra dedicada ao sobrenome) ou "ner" (spaCy)
    confidence: float


@dataclass(frozen=True)
class PlaceMention:
    name_raw: str
    start_offset: int
    end_offset: int
    source: str  # "ner"
    confidence: float


def find_surname_person(text: str, surname: str) -> PersonMention | None:
    """Localiza o nome completo ao redor da ocorrência do sobrenome,
    expandindo pra tokens vizinhos que parecem parte do nome (capitalizados,
    conectores como "de"/"e", títulos como "Sr."/"D."). Não depende de NER.
    """
    needle = _strip_accents(surname).lower()
    haystack = _strip_accents(text).lower()
    match_start = haystack.find(needle)
    if match_start == -1:
        return None
    match_end = match_start + len(needle)

    # tokens com posição, pra poder expandir e depois recalcular offsets no texto original
    tokens = list(re.finditer(r"\S+", text))
    idx_in = next((i for i, t in enumerate(tokens) if t.start() <= match_start < t.end()), None)
    if idx_in is None:
        return None

    start_i, end_i = idx_in, idx_in
    MAX_EXPAND = 4

    steps = 0
    while start_i > 0 and steps < MAX_EXPAND:
        candidate = tokens[start_i - 1].group()
        if _ends_list_item(candidate) or not _looks_like_name_token(candidate):
            break
        start_i -= 1
        steps += 1

    steps = 0
    if not _ends_list_item(tokens[end_i].group()):
        while end_i < len(tokens) - 1 and steps < MAX_EXPAND:
            candidate = tokens[end_i + 1].group()
            if not _looks_like_name_token(candidate):
                break
            end_i += 1
            steps += 1
            if _ends_list_item(candidate):
                break

    span_start = tokens[start_i].start()
    span_end = tokens[end_i].end()
    name_raw = text[span_start:span_end].strip(" ,;:.")
    return PersonMention(
        name_raw=name_raw, start_offset=span_start, end_offset=span_end,
        source="ancora", confidence=0.85,
    )


@lru_cache(maxsize=1)
def _load_nlp():
    import spacy

    return spacy.load("pt_core_news_sm")


def extract_people_and_places(text: str) -> tuple[list[PersonMention], list[PlaceMention]]:
    """NER genérico (spaCy) pras outras pessoas e lugares mencionados no
    trecho, além do nome ancorado no sobrenome (que usa `find_surname_person`).
    Qualidade observada no corpus é irregular — tratar como sinal auxiliar,
    não como fonte principal.
    """
    nlp = _load_nlp()
    doc = nlp(text)
    people, places = [], []
    for ent in doc.ents:
        if ent.label_ == "PER":
            people.append(PersonMention(
                name_raw=ent.text, start_offset=ent.start_char, end_offset=ent.end_char,
                source="ner", confidence=0.5,
            ))
        elif ent.label_ in ("LOC", "GPE"):
            places.append(PlaceMention(
                name_raw=ent.text, start_offset=ent.start_char, end_offset=ent.end_char,
                source="ner", confidence=0.5,
            ))
    return people, places
