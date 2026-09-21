"""Construção do grafo de relações familiares (Fase 6).

Nós = pessoas candidatas. Uma menção do sobrenome buscado é resolvida pro
nó do seu cluster da Fase 5 (mesma pessoa hipotética, segundo aquele
agrupamento); outros nomes citados como sujeito/alvo de uma relação (pais,
cônjuges sem o sobrenome buscado) viram nós próprios, um por nome
normalizado — sem clustering fuzzy pra esses, então grafias diferentes do
mesmo nome podem aparecer como nós separados (limitação conhecida, não
resolvida nesta fase).

Cada aresta carrega origem (`occurrence_id`, URL da fonte), evidência (o
trecho que a originou) e status (`extraida` | `confirmada` | `rejeitada`,
puxado da revisão feita no app da Fase 4 quando existir — senão o status
que a Fase 3 atribuiu). Uma aresta rejeitada pelo revisor não entra no
grafo. Nenhuma aresta é inferida por coocorrência — só as relações que a
Fase 3 já extraiu explicitamente do texto chegam aqui.
"""

from __future__ import annotations

import networkx as nx

from src.resolution.features import normalize_name


def _cluster_lookup(cluster_report: dict) -> dict[str, str]:
    """normalized_name -> node_id, usando os clusters da Fase 5 (método por regra)."""
    lookup: dict[str, str] = {}
    for cluster in cluster_report.get("clusters_por_regra", []):
        node_id = f"cluster:{cluster['cluster_id']}"
        for member in cluster["membros"]:
            lookup[normalize_name(member["name_raw"])] = node_id
    return lookup


def _node_id_for(name_raw: str, cluster_lookup: dict[str, str]) -> str:
    norm = normalize_name(name_raw)
    return cluster_lookup.get(norm, f"nome:{norm}")


def build_graph(
    occurrences: list[dict],
    cluster_report: dict | None = None,
    review: dict | None = None,
) -> nx.MultiDiGraph:
    cluster_lookup = _cluster_lookup(cluster_report) if cluster_report else {}
    review = review or {}
    graph = nx.MultiDiGraph()

    def ensure_node(node_id: str, label: str) -> None:
        if node_id not in graph:
            graph.add_node(node_id, label=label)

    for occ in occurrences:
        occ_review = review.get(occ["id"], {})
        for i, rel in enumerate(occ.get("relations", [])):
            if not rel.get("subject_raw"):
                continue  # aresta precisa de um "quem" conhecido

            subject_id = _node_id_for(rel["subject_raw"], cluster_lookup)
            target_id = _node_id_for(rel["target_raw"], cluster_lookup)
            if subject_id == target_id:
                continue  # auto-relação: provável erro de extração, não entra

            status = occ_review.get("relations", {}).get(str(i), rel.get("status", "extraida"))
            if status == "rejeitada":
                continue

            ensure_node(subject_id, rel["subject_raw"])
            ensure_node(target_id, rel["target_raw"])
            graph.add_edge(
                subject_id, target_id,
                relation=rel["relation"], status=status, confidence=rel["confidence"],
                evidence=rel["evidence"], occurrence_id=occ["id"], source_url=occ["source_url"],
                periodical=occ.get("periodical"), year=occ.get("year"),
            )

    return graph
