"""Gera uma visualização melhor do item 3 (busca e recomendação) em cima do
`resultados_busca.json` já salvo — não roda buscas de novo, só lê o que já
existe e produz:

1. Um gráfico (`figuras/busca_queda_relevancia.png`) com um painel por
   consulta, mostrando como o score cai do rank 1 ao 5 em cada
   representação (normalizado pelo próprio top-1 de cada uma, já que as
   escalas de BoW/TF-IDF e Word2Vec/BERT não são comparáveis em valor
   absoluto — o que importa aqui é o *formato* da curva: queda abrupta pra
   zero vs. queda suave).
2. Um relatório HTML autocontido (`relatorio_busca.html`) com uma seção por
   consulta e uma tabela por representação (rank, score, tipo de evento,
   texto), mais fácil de ler que o JSON cru.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
JSON_PATH = OUT_DIR / "resultados_busca.json"
IMG_DIR = OUT_DIR / "figuras"
IMG_DIR.mkdir(exist_ok=True)

REPRESENTACOES = ["bow", "tfidf", "word2vec", "bert"]
CORES = {"bow": "#1f77b4", "tfidf": "#ff7f0e", "word2vec": "#2ca02c", "bert": "#d62728"}

CORES_EVENTO = {
    "casamento": "#2e7d32",
    "obito": "#555555",
    "nascimento": "#1565c0",
    "outro": "#9e9e9e",
}


def gerar_grafico(dados: dict) -> Path:
    consultas = list(dados["consultas"].keys())
    fig, eixos = plt.subplots(2, 2, figsize=(11, 8))
    eixos = eixos.flatten()

    for ax, consulta in zip(eixos, consultas):
        for rep in REPRESENTACOES:
            itens = dados["consultas"][consulta][rep]
            scores = np.array([it["score"] for it in itens], dtype=float)
            topo = scores[0] if scores[0] > 0 else 1.0
            scores_norm = scores / topo
            ax.plot(
                range(1, len(scores) + 1), scores_norm,
                marker="o", label=rep, color=CORES[rep],
            )
        ax.set_title(consulta, fontsize=10)
        ax.set_xlabel("rank")
        ax.set_ylabel("score / score do rank 1")
        ax.set_xticks(range(1, 6))
        ax.set_ylim(-0.05, 1.05)

    eixos[0].legend(loc="upper right", fontsize=8)
    fig.suptitle(
        "Queda de relevância do rank 1 ao 5, normalizada por consulta/representação\n"
        "(BoW/TF-IDF caem a zero assim que não há mais sobreposição de vocabulário; "
        "Word2Vec/BERT caem de forma suave)",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))

    caminho = IMG_DIR / "busca_queda_relevancia.png"
    fig.savefig(caminho, dpi=130)
    plt.close(fig)
    return caminho


def gerar_html(dados: dict, caminho_grafico: Path) -> Path:
    partes = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<title>Item 3 — Busca e recomendação</title>",
        "<style>",
        "body{font-family:system-ui,Arial,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222;}",
        "h1{font-size:1.6rem} h2{margin-top:2.5rem;border-bottom:2px solid #ddd;padding-bottom:.3rem}",
        "h3{margin-top:1.5rem;color:#444}",
        "table{border-collapse:collapse;width:100%;margin-bottom:1rem;font-size:.9rem}",
        "th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top}",
        "th{background:#f3f3f3}",
        "tr:nth-child(even){background:#fafafa}",
        ".badge{display:inline-block;padding:.1rem .5rem;border-radius:999px;color:#fff;font-size:.75rem}",
        ".score{font-variant-numeric:tabular-nums;font-weight:600}",
        ".rep{font-weight:700;text-transform:uppercase;font-size:.85rem;color:#555}",
        "img{max-width:100%;border:1px solid #ddd;border-radius:6px;margin:1rem 0}",
        "</style></head><body>",
        "<h1>Item 3 — Busca e recomendação (visualização)</h1>",
        f"<p>{dados['n_documentos']} documentos no corpus. Gerado a partir de "
        "<code>resultados_busca.json</code> — ver <code>resultados_busca.md</code> "
        "para a análise crítica completa.</p>",
        f"<img src='{caminho_grafico.relative_to(OUT_DIR).as_posix()}' alt='gráfico de queda de relevância'>",
    ]

    for consulta, por_rep in dados["consultas"].items():
        partes.append(f"<h2>Consulta: “{escape(consulta)}”</h2>")
        for rep in REPRESENTACOES:
            partes.append(f"<h3 class='rep'>{rep}</h3>")
            partes.append("<table><tr><th>#</th><th>score</th><th>evento</th><th>ano</th><th>texto</th></tr>")
            for item in por_rep[rep]:
                cor = CORES_EVENTO.get(item["event_type"], "#9e9e9e")
                partes.append(
                    "<tr>"
                    f"<td>{item['rank']}</td>"
                    f"<td class='score'>{item['score']:.3f}</td>"
                    f"<td><span class='badge' style='background:{cor}'>{escape(item['event_type'])}</span></td>"
                    f"<td>{item['ano']}</td>"
                    f"<td>{escape(item['texto'])}</td>"
                    "</tr>"
                )
            partes.append("</table>")

    partes.append("</body></html>")

    caminho = OUT_DIR / "relatorio_busca.html"
    caminho.write_text("\n".join(partes), encoding="utf-8")
    return caminho


def main() -> None:
    dados = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    caminho_grafico = gerar_grafico(dados)
    print(f"gráfico salvo em {caminho_grafico}")
    caminho_html = gerar_html(dados, caminho_grafico)
    print(f"relatório salvo em {caminho_html}")


if __name__ == "__main__":
    main()
