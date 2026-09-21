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
from pathlib import Path

from src.extraction.dates import extract_dates
from src.extraction.entities import extract_people_and_places, find_surname_person
from src.extraction.events import classify_event
from src.extraction.relations import extract_relations

logger = logging.getLogger("extraction.pipeline")


def extract_from_occurrence(occurrence: dict) -> dict:
    excerpt = occurrence["context_excerpt"]
    surname = occurrence["query"]

    surname_person = find_surname_person(excerpt, surname)
    people, places = extract_people_and_places(excerpt)
    event = classify_event(excerpt)
    dates = extract_dates(excerpt)
    relations = extract_relations(occurrence.get("text_normalized", excerpt))

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
        for line in fin:
            occurrence = json.loads(line)
            extracted = extract_from_occurrence(occurrence)
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
