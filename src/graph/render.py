"""Renderização interativa do grafo de relações (Fase 6) — pyvis/vis.js.

Diferencia visualmente relações extraídas (hipótese, tracejado laranja)
de confirmadas (sólido verde); nós e arestas com ciclo ou incompatibilidade
temporal sinalizada ficam em vermelho. Passar o mouse mostra a evidência
e o link da fonte — nenhuma aresta aparece sem essas informações.
"""

from __future__ import annotations

import networkx as nx
from pyvis.network import Network

STATUS_COLORS = {"extraida": "#f0ad4e", "confirmada": "#5cb85c"}
FLAG_COLOR = "#d9534f"
DEFAULT_NODE_COLOR = "#5b9bd5"


def render_graph_html(
    graph: nx.MultiDiGraph,
    cycles: list[list[str]],
    temporal_flags: list[dict],
    height: str = "650px",
) -> str:
    net = Network(height=height, width="100%", directed=True, notebook=False, cdn_resources="in_line")
    net.barnes_hut()

    flagged_nodes = {n for cycle in cycles for n in cycle}
    flagged_pairs = {(f["filho"], f["pai_ou_mae"]) for f in temporal_flags}
    flag_by_pair = {(f["filho"], f["pai_ou_mae"]): f["aviso"] for f in temporal_flags}

    for node, data in graph.nodes(data=True):
        label = data.get("label", node)
        color = FLAG_COLOR if node in flagged_nodes else DEFAULT_NODE_COLOR
        title = label + ("\n⚠ participa de um ciclo de filiação (logicamente impossível)" if node in flagged_nodes else "")
        net.add_node(node, label=label[:30], title=title, color=color)

    for u, v, data in graph.edges(data=True):
        status = data.get("status", "extraida")
        flagged = (u, v) in flagged_pairs
        color = FLAG_COLOR if flagged else STATUS_COLORS.get(status, "#999999")
        title_lines = [
            f"{data['relation']} ({status}, confiança {data['confidence']:.0%})",
            f"Evidência: {data['evidence']}",
            f"Fonte: {data['source_url']}",
        ]
        if flagged:
            title_lines.append(f"⚠ {flag_by_pair[(u, v)]}")
        net.add_edge(
            u, v, label=data["relation"], color=color,
            dashes=(status != "confirmada"), title="\n".join(title_lines), arrows="to",
        )

    return net.generate_html()
