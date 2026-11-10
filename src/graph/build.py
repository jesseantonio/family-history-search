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


class _ClusterLookup:
    """Resolve uma menção para o nó do seu cluster da Fase 5 (método por regra).

    A chave principal é (ocorrência, nome): duas pessoas homônimas em clusters
    diferentes continuam separadas, porque cada menção sabe de que ocorrência veio.
    Só o nome, sem a ocorrência, é usado quando ele pertence a um único cluster;
    nome ambíguo sem ocorrência correspondente vira um nó próprio por nome."""

    def __init__(self, cluster_report: dict | None):
        self.by_occurrence: dict[tuple[str, str], str] = {}
        by_name: dict[str, set[str]] = {}
        for cluster in (cluster_report or {}).get("clusters_por_regra", []):
            node_id = f"cluster:{cluster['cluster_id']}"
            for member in cluster["membros"]:
                norm = normalize_name(member["name_raw"])
                self.by_occurrence[(member["occurrence_id"], norm)] = node_id
                by_name.setdefault(norm, set()).add(node_id)
        self.unique_by_name = {n: next(iter(ids)) for n, ids in by_name.items() if len(ids) == 1}

    def node_id(self, occurrence_id: str, name_raw: str) -> str:
        norm = normalize_name(name_raw)
        return (self.by_occurrence.get((occurrence_id, norm))
                or self.unique_by_name.get(norm)
                or f"nome:{norm}")


def build_graph(
    occurrences: list[dict],
    cluster_report: dict | None = None,
    review: dict | None = None,
) -> nx.MultiDiGraph:
    lookup = _ClusterLookup(cluster_report)
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

            subject_id = lookup.node_id(occ["id"], rel["subject_raw"])
            target_id = lookup.node_id(occ["id"], rel["target_raw"])
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
                evidence=rel["evidence"], occurrence_id=occ["id"], relation_index=i,
                source_url=occ["source_url"],
                periodical=occ.get("periodical"), year=occ.get("year"),
            )

    return graph
