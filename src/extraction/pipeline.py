"""Junta datas, tipo de evento, pessoas, lugares e relações (Fase 3) para
cada ocorrência já processada pela Fase 2 (precisa de `context_excerpt` e
`text_normalized`).

Não há amostra anotada (Fase 0) pra medir precisão/cobertura/F1 como o
README pede — essa validação depende de anotação manual, que não foi
feita. O que este módulo garante é rastreabilidade: toda extração carrega
sua evidência (o trecho que a originou) e uma confiança, nunca um "fato"
sem fonte.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator

from src.extraction.dates import extract_dates
from src.extraction.entities import (extract_people_and_places, extract_people_and_places_many,
                                     find_surname_person)
from src.extraction.events import classify_event
from src.extraction.relations import extract_relations

logger = logging.getLogger("extraction.pipeline")

CHUNK_LINES = 512


def _chunks(lines: Iterable[str], n: int) -> Iterator[list[str]]:
    it = iter(lines)
    while chunk := list(islice(it, n)):
        yield chunk


def extract_from_occurrence(occurrence: dict, people_places: tuple | None = None) -> dict:
    """`people_places`, se informado, é o resultado já calculado de
    `extract_people_and_places(occurrence["context_excerpt"])` (ver `main`, que roda o
    NER em lote)."""
    excerpt = occurrence["context_excerpt"]
    surname = occurrence["query"]

    surname_person = find_surname_person(excerpt, surname)
    people, places = people_places if people_places is not None else extract_people_and_places(excerpt)
    event = classify_event(excerpt)
    # datas relativas ("hontem", "sabbado passado") e sem ano são resolvidas pela data de
    # publicação da edição; sem ela, o ano da edição completa só as datas sem ano
    dates = extract_dates(excerpt, occurrence.get("publication_date"), occurrence.get("year"))
    # só o trecho em volta do sobrenome: a página inteira traz outras notícias, e um
    # "filho de" de outra notícia viraria uma aresta falsa no grafo
    relations = extract_relations(excerpt)

    return {
        **occurrence,
        "person_anchor": asdict(surname_person) if surname_person else None,
        "people_mentioned": [asdict(p) for p in people],
        "places_mentioned": [asdict(p) for p in places],
        "event_type": event.event_type,
        "event_type_evidence": event.evidence,
        "event_type_confidence": event.confidence,
        "dates": [{**asdict(d), "date_value": d.date_value} for d in dates],
        "relations": [asdict(r) for r in relations],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fase 3: extrai pessoas, datas, lugares, eventos e relações das ocorrências da Fase 2."
    )
    parser.add_argument("--in", dest="input", type=Path, required=True, help="occurrences_*_enriched.jsonl da Fase 2")
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)

    total = 0
    com_relacao = 0
    com_data = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding="utf-8") as fin, args.out.open("w", encoding="utf-8") as fout:
        # lê em blocos para rodar o NER em lote sem carregar o arquivo inteiro na memória
        for chunk in _chunks(fin, CHUNK_LINES):
            occurrences = [json.loads(line) for line in chunk]
            ners = extract_people_and_places_many([o["context_excerpt"] for o in occurrences])
            for occurrence, ner in zip(occurrences, ners):
                extracted = extract_from_occurrence(occurrence, ner)
                if extracted["relations"]:
                    com_relacao += 1
                if extracted["dates"]:
                    com_data += 1
                fout.write(json.dumps(extracted, ensure_ascii=False) + "\n")
                total += 1

    logger.info(
        "%d ocorrência(s) processadas — %d com data, %d com relação extraída — salvo em %s",
        total, com_data, com_relacao, args.out,
    )


if __name__ == "__main__":
    main()
