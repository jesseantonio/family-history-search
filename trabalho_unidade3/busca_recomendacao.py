"""Item 3 do trabalho: busca e recomendação sobre o corpus de ocorrências,
comparando BoW, TF-IDF, Word2Vec (treinado do zero) e BERT (pré-treinado).

Roda 4 consultas, pensadas pra cobrir ângulos diferentes do corpus (ver
`resultados_busca.md` pra análise crítica dos resultados), calcula o top-5
por representação (cosseno) e salva tudo em `resultados_busca.json` pra
servir de base real pro texto da análise — nenhum resultado aqui é
inventado, é saída direta deste script.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from trabalho_unidade3.corpus import carregar_corpus
from trabalho_unidade3.representacoes import (
    buscar,
    construir_bert,
    construir_bow,
    construir_tfidf,
    construir_word2vec,
)

CONSULTAS = [
    "casamento convite núpcias",
    "falecimento sepultamento morte",
    "aniversariante parabéns",
    "Effting",
]

OUT_PATH = Path(__file__).resolve().parent / "resultados_busca.json"


def main() -> None:
    docs = carregar_corpus()
    textos = [d.texto for d in docs]
    print(f"{len(docs)} documentos carregados")

    print("construindo BoW...")
    rep_bow = construir_bow(textos)

    print("construindo TF-IDF...")
    rep_tfidf = construir_tfidf(textos)

    print("treinando Word2Vec (pode levar ~2min)...")
    t0 = time.time()
    rep_w2v, _modelo_w2v = construir_word2vec(
        textos, vector_size=50, window=4, negative=5, epochs=40,
    )
    print(f"  ... treinado em {time.time() - t0:.1f}s, vocabulário: {len(_modelo_w2v.idx2word)} palavras")

    print("carregando BERT (baixa o modelo na primeira vez, ~470MB)...")
    rep_bert = construir_bert(textos)

    representacoes = [rep_bow, rep_tfidf, rep_w2v, rep_bert]

    resultados = {"n_documentos": len(docs), "consultas": {}}
    for consulta in CONSULTAS:
        print(f"\n=== consulta: {consulta!r} ===")
        resultados["consultas"][consulta] = {}
        for rep in representacoes:
            top5 = buscar(rep, consulta, docs, top_k=5)
            resultados["consultas"][consulta][rep.nome] = top5
            print(f"-- {rep.nome} (top-3 de 5) --")
            for item in top5[:3]:
                print(f"   [{item['score']:.3f}] ({item['event_type']}) {item['texto'][:90]}")

    OUT_PATH.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresultados completos salvos em {OUT_PATH}")


if __name__ == "__main__":
    main()
