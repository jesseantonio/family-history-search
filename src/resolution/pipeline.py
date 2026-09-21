"""Orquestra a Fase 5: constrói as menções, calcula o score par-a-par,
agrupa por dois métodos e produz um relatório revisável — cada cluster
carrega a evidência (score por característica) que o justifica.

Sem uma amostra revisada manualmente (Fase 0), não há "verdade" pra medir
B-cubed contra. O relatório compara os dois métodos ENTRE SI (quantos
grupos cada um formou, em quais menções eles discordam) — não contra um
gabarito que não existe.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path

from src.resolution.clustering import (
    DEFAULT_THRESHOLD,
    cluster_by_threshold,
    cluster_hierarchical,
    compute_all_pairs,
)
from src.resolution.features import PersonMentionRecord, build_mentions

logger = logging.getLogger("resolution.pipeline")


def _mention_summary(m: PersonMentionRecord) -> dict:
    return {
        "mention_id": m.mention_id,
        "occurrence_id": m.occurrence_id,
        "name_raw": m.name_raw,
        "source": m.source,
        "year": m.year,
        "year_precision": m.year_precision,
        "periodical": m.periodical,
        "context_excerpt": m.context_excerpt,
        "source_url": m.source_url,
    }


def _clusters_report(clusters: list[list[int]], mentions: list[PersonMentionRecord], pairs_by_key: dict) -> list[dict]:
    report = []
    for cluster_id, member_idxs in enumerate(sorted(clusters, key=len, reverse=True)):
        evidence = []
        for a in range(len(member_idxs)):
            for b in range(a + 1, len(member_idxs)):
                i, j = sorted((member_idxs[a], member_idxs[b]))
                breakdown = pairs_by_key.get((i, j))
                if breakdown:
                    evidence.append({
                        "mention_a": mentions[i].mention_id, "mention_b": mentions[j].mention_id,
                        "score": round(breakdown.total, 3),
                        "detalhes": {
                            "similaridade_nome": round(breakdown.name_similarity, 3),
                            "pessoas_em_comum": round(breakdown.people_in_common, 3),
                            "proximidade_geografica": round(breakdown.place_proximity, 3),
                            "compatibilidade_temporal": round(breakdown.temporal_compatibility, 3),
                        },
                    })
        report.append({
            "cluster_id": cluster_id,
            "tamanho": len(member_idxs),
            "membros": [_mention_summary(mentions[i]) for i in member_idxs],
            "evidencia_par_a_par": evidence,
        })
    return report


def _cluster_membership(clusters: list[list[int]], n: int) -> list[int]:
    membership = [-1] * n
    for cluster_id, members in enumerate(clusters):
        for idx in members:
            membership[idx] = cluster_id
    return membership


def run(occurrences: list[dict], surname: str, threshold: float = DEFAULT_THRESHOLD) -> dict:
    mentions = build_mentions(occurrences, surname)
    pairs = compute_all_pairs(mentions)
    pairs_by_key = {(p.i, p.j): p.breakdown for p in pairs}

    clusters_rule = cluster_by_threshold(len(mentions), pairs, threshold)
    clusters_hier = cluster_hierarchical(len(mentions), pairs, threshold)

    membership_rule = _cluster_membership(clusters_rule, len(mentions))
    membership_hier = _cluster_membership(clusters_hier, len(mentions))

    mates_rule = [frozenset(clusters_rule[membership_rule[i]]) - {i} for i in range(len(mentions))]
    mates_hier = [frozenset(clusters_hier[membership_hier[i]]) - {i} for i in range(len(mentions))]
    discordant = [
        mentions[i].mention_id for i in range(len(mentions))
        if mates_rule[i] != mates_hier[i]
    ]

    return {
        "surname": surname,
        "total_mentions": len(mentions),
        "threshold": threshold,
        "clusters_por_regra": _clusters_report(clusters_rule, mentions, pairs_by_key),
        "clusters_hierarquico": _clusters_report(clusters_hier, mentions, pairs_by_key),
        "comparacao": {
            "num_clusters_regra": len(clusters_rule),
            "num_clusters_hierarquico": len(clusters_hier),
            "mencoes_com_agrupamento_diferente": discordant,
            "pct_discordancia": round(100 * len(discordant) / len(mentions), 1) if mentions else 0.0,
        },
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fase 5: agrupa menções de pessoa por similaridade.")
    parser.add_argument("--in", dest="input", type=Path, required=True, help="occurrences_*_extracted.jsonl da Fase 3")
    parser.add_argument("--surname", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)

    with args.input.open(encoding="utf-8") as f:
        occurrences = [json.loads(line) for line in f]

    report = run(occurrences, args.surname, args.threshold)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "%d menções — %d clusters (regra), %d clusters (hierárquico), %.1f%% discordância — salvo em %s",
        report["total_mentions"], report["comparacao"]["num_clusters_regra"],
        report["comparacao"]["num_clusters_hierarquico"], report["comparacao"]["pct_discordancia"], args.out,
    )


if __name__ == "__main__":
    main()
