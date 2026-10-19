"""Item 2 do trabalho: clusterização (K-Means) + PCA + visualização, sobre
as mesmas quatro representações do item 3 (BoW, TF-IDF, Word2Vec, BERT).

Pra cada representação:
  1. Testa K de 2 a 8 com K-Means, escolhe o K com melhor silhouette score.
  2. Roda o K-Means final com esse K.
  3. Projeta em 2D via PCA (só pra visualização — o clustering em si roda no
     espaço original de cada representação, não no espaço reduzido).
  4. Salva o gráfico de dispersão colorido por cluster (PNG).
  5. Calcula métricas intra-cluster (inércia/WCSS, distância média ao
     centroide) e inter-cluster (silhouette score geral, distância entre
     centroides).
  6. Lista exemplos reais de documentos de cada cluster, pra leitura
     qualitativa.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_samples, silhouette_score

from trabalho_unidade3.corpus import carregar_corpus
from trabalho_unidade3.representacoes import (
    construir_bert,
    construir_bow,
    construir_tfidf,
    construir_word2vec,
)

OUT_DIR = Path(__file__).resolve().parent
IMG_DIR = OUT_DIR / "figuras"
IMG_DIR.mkdir(exist_ok=True)

K_CANDIDATOS = range(2, 9)


def escolher_melhor_k(matriz: np.ndarray, seed: int = 42) -> tuple[int, dict[int, float]]:
    scores: dict[int, float] = {}
    for k in K_CANDIDATOS:
        labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(matriz)
        scores[k] = float(silhouette_score(matriz, labels))
    melhor_k = max(scores, key=scores.get)
    return melhor_k, scores


def clusterizar_e_visualizar(nome: str, matriz: np.ndarray, docs, seed: int = 42) -> dict:
    melhor_k, scores_por_k = escolher_melhor_k(matriz, seed)

    kmeans = KMeans(n_clusters=melhor_k, random_state=seed, n_init=10).fit(matriz)
    labels = kmeans.labels_

    sil_geral = float(silhouette_score(matriz, labels))
    sil_por_doc = silhouette_samples(matriz, labels)

    # Intra-cluster: distância média de cada ponto ao centroide do seu cluster
    intra_por_cluster = {}
    for c in range(melhor_k):
        pontos = matriz[labels == c]
        centroide = kmeans.cluster_centers_[c]
        dists = np.linalg.norm(pontos - centroide, axis=1)
        intra_por_cluster[c] = float(dists.mean())

    # Inter-cluster: distância entre os centroides (matriz completa)
    centroides = kmeans.cluster_centers_
    dist_centroides = np.linalg.norm(
        centroides[:, None, :] - centroides[None, :, :], axis=-1
    )

    # PCA só pra visualização 2D
    pca = PCA(n_components=2, random_state=seed)
    coords2d = pca.fit_transform(matriz)
    var_explicada = pca.explained_variance_ratio_.tolist()

    fig, ax = plt.subplots(figsize=(8, 6))
    scatter = ax.scatter(
        coords2d[:, 0], coords2d[:, 1], c=labels, cmap="tab10", s=25, alpha=0.75,
    )
    ax.set_title(f"{nome.upper()} — K-Means (k={melhor_k}) projetado via PCA")
    ax.set_xlabel(f"PC1 ({var_explicada[0]*100:.1f}% da variância)")
    ax.set_ylabel(f"PC2 ({var_explicada[1]*100:.1f}% da variância)")
    legenda = ax.legend(*scatter.legend_elements(), title="cluster", loc="best")
    ax.add_artist(legenda)
    fig.tight_layout()
    caminho_img = IMG_DIR / f"cluster_{nome}.png"
    fig.savefig(caminho_img, dpi=130)
    plt.close(fig)

    # Exemplos reais por cluster (até 3 por cluster, os mais "centrais" —
    # menor distância ao centroide — pra leitura qualitativa representativa)
    exemplos_por_cluster = {}
    for c in range(melhor_k):
        idxs = np.where(labels == c)[0]
        centroide = kmeans.cluster_centers_[c]
        dists = np.linalg.norm(matriz[idxs] - centroide, axis=1)
        ordem = idxs[np.argsort(dists)][:3]
        exemplos_por_cluster[c] = [
            {"texto": docs[i].texto, "event_type": docs[i].event_type, "ano": docs[i].ano}
            for i in ordem
        ]

    return {
        "representacao": nome,
        "melhor_k": melhor_k,
        "silhouette_por_k": scores_por_k,
        "silhouette_geral": sil_geral,
        "silhouette_medio_por_cluster": {
            c: float(sil_por_doc[labels == c].mean()) for c in range(melhor_k)
        },
        "distancia_intra_cluster": intra_por_cluster,
        "distancia_entre_centroides": dist_centroides.tolist(),
        "tamanho_clusters": {c: int((labels == c).sum()) for c in range(melhor_k)},
        "variancia_explicada_pca": var_explicada,
        "imagem": str(caminho_img.relative_to(OUT_DIR)),
        "exemplos_por_cluster": exemplos_por_cluster,
    }


def main() -> None:
    docs = carregar_corpus()
    textos = [d.texto for d in docs]
    print(f"{len(docs)} documentos carregados")

    print("construindo representações...")
    rep_bow = construir_bow(textos)
    rep_tfidf = construir_tfidf(textos)
    rep_w2v, _ = construir_word2vec(textos, vector_size=50, window=4, negative=5, epochs=40)
    rep_bert = construir_bert(textos)

    resultados = {}
    for rep in [rep_bow, rep_tfidf, rep_w2v, rep_bert]:
        print(f"\nclusterizando {rep.nome}...")
        resultado = clusterizar_e_visualizar(rep.nome, rep.matriz_docs, docs)
        resultados[rep.nome] = resultado
        print(
            f"  melhor k={resultado['melhor_k']}, "
            f"silhouette geral={resultado['silhouette_geral']:.3f}, "
            f"tamanhos dos clusters={resultado['tamanho_clusters']}"
        )

    out_path = OUT_DIR / "resultados_clusterizacao.json"
    out_path.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresultados salvos em {out_path}")
    print(f"imagens salvas em {IMG_DIR}")


if __name__ == "__main__":
    main()
