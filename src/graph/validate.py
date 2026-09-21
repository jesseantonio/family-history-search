"""Validação do grafo de relações (Fase 6): ciclos e incompatibilidades
óbvias em filiação. Nada é removido automaticamente — só sinalizado, pra
quem revisa decidir. Um ciclo em `filho_de` (A filho de B, B filho de A)
é logicamente impossível; uma "menção mais antiga" do filho do que do
pai/mãe alegado é um sinal fraco (usa ano da ocorrência do jornal, não
data de nascimento), mas ainda vale levantar a suspeita visualmente.
"""

from __future__ import annotations

import networkx as nx


def find_filiation_cycles(graph: nx.MultiDiGraph) -> list[list[str]]:
    filho_de_edges = [
        (u, v) for u, v, d in graph.edges(data=True)
        if d.get("relation") == "filho_de"
    ]
    subgraph = nx.DiGraph(filho_de_edges)
    return list(nx.simple_cycles(subgraph))


def find_temporal_incompatibilities(graph: nx.MultiDiGraph) -> list[dict]:
    node_min_year: dict[str, int] = {}
    for u, v, d in graph.edges(data=True):
        year = d.get("year")
        if not year:
            continue
        for node in (u, v):
            if node not in node_min_year or year < node_min_year[node]:
                node_min_year[node] = year

    flags = []
    for u, v, d in graph.edges(data=True):
        if d.get("relation") != "filho_de":
            continue
        child_year, parent_year = node_min_year.get(u), node_min_year.get(v)
        if child_year and parent_year and child_year < parent_year:
            flags.append({
                "filho": u, "pai_ou_mae": v,
                "ano_mais_antigo_filho": child_year, "ano_mais_antigo_pai_mae": parent_year,
                "aviso": (
                    "menção do filho é mais antiga que a do pai/mãe alegado — sinal fraco, "
                    "baseado no ano da ocorrência do jornal, não na data de nascimento"
                ),
            })
    return flags
