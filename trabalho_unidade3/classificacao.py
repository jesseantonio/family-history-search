"""Item 4 do trabalho: tentativa de treinar um classificador supervisionado
sobre as quatro representações, usando o `event_type` já extraído na
Unidade 1 como rótulo (único rótulo disponível no dataset).

Aviso de antemão (e confirmado pelos números abaixo): o rótulo é
extremamente desbalanceado — de 253 documentos, 248 são "outro" e só 5
estão distribuídos entre `nascimento` (2), `casamento` (1) e `obito` (2).
Isso inviabiliza uma divisão treino/teste tradicional (a classe `casamento`
tem um único exemplo no corpus inteiro). Em vez de simular uma divisão que
escondesse esse problema, usamos *leave-one-out* cross-validation (LOO):
cada documento é testado individualmente com um modelo treinado em TODOS os
outros — é a validação mais generosa possível com dados tão escassos, e
ainda assim (como os resultados mostram) o classificador não consegue
aprender as classes minoritárias, porque quando a única amostra de
`casamento` é o item de teste, sobra ZERO exemplo de `casamento` pra
treinar.

Testa duas técnicas de classificação, pra cobrir o pedido do enunciado:
- Regressão Logística sobre embeddings de BERT (técnica usando modelo de
  linguagem).
- Árvore de decisão sobre BoW (técnica clássica baseada em modelo de
  decisão).
E Regressão Logística também sobre BoW/TF-IDF/Word2Vec, pra comparar as
representações entre si sob a mesma técnica.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import LeaveOneOut
from sklearn.tree import DecisionTreeClassifier

from trabalho_unidade3.corpus import carregar_corpus
from trabalho_unidade3.representacoes import (
    construir_bert,
    construir_bow,
    construir_tfidf,
    construir_word2vec,
)

OUT_PATH = Path(__file__).resolve().parent / "resultados_classificacao.json"


def avaliar_loo(matriz: np.ndarray, y: np.ndarray, modelo_fn) -> dict:
    """Leave-one-out: pra cada amostra, treina em todas as outras e testa
    nela. Devolve previsões, relatório de classificação e matriz de
    confusão."""
    loo = LeaveOneOut()
    y_pred = np.empty_like(y)

    for treino_idx, teste_idx in loo.split(matriz):
        modelo = modelo_fn()
        modelo.fit(matriz[treino_idx], y[treino_idx])
        y_pred[teste_idx] = modelo.predict(matriz[teste_idx])

    rotulos = sorted(set(y))
    relatorio = classification_report(
        y, y_pred, labels=rotulos, output_dict=True, zero_division=0
    )
    matriz_confusao = confusion_matrix(y, y_pred, labels=rotulos).tolist()

    return {
        "relatorio": relatorio,
        "matriz_confusao": matriz_confusao,
        "rotulos": rotulos,
        "acuracia": float((y_pred == y).mean()),
    }


def main() -> None:
    docs = carregar_corpus()
    textos = [d.texto for d in docs]
    y = np.array([d.event_type for d in docs])

    distrib = {rotulo: int((y == rotulo).sum()) for rotulo in sorted(set(y))}
    print("distribuição de event_type:", distrib)

    print("construindo representações...")
    rep_bow = construir_bow(textos)
    rep_tfidf = construir_tfidf(textos)
    rep_w2v, _ = construir_word2vec(textos, vector_size=50, window=4, negative=5, epochs=40)
    rep_bert = construir_bert(textos)

    resultados: dict = {"distribuicao_event_type": distrib}

    print("\n=== Regressão Logística (LOO) por representação ===")
    for rep in [rep_bow, rep_tfidf, rep_w2v, rep_bert]:
        print(f"-- {rep.nome} --")
        r = avaliar_loo(
            rep.matriz_docs, y,
            lambda: LogisticRegression(max_iter=2000, class_weight="balanced"),
        )
        print(f"   acurácia: {r['acuracia']:.3f}  |  macro-F1: {r['relatorio']['macro avg']['f1-score']:.3f}")
        for rotulo in r["rotulos"]:
            m = r["relatorio"][rotulo]
            print(f"     {rotulo:12s} precisão={m['precision']:.2f} revocação={m['recall']:.2f} f1={m['f1-score']:.2f} (n={int(m['support'])})")
        resultados[f"logreg_{rep.nome}"] = r

    print("\n=== Árvore de decisão (LOO) sobre BoW ===")
    r_tree = avaliar_loo(rep_bow.matriz_docs, y, lambda: DecisionTreeClassifier(random_state=42, class_weight="balanced"))
    print(f"   acurácia: {r_tree['acuracia']:.3f}  |  macro-F1: {r_tree['relatorio']['macro avg']['f1-score']:.3f}")
    resultados["arvore_decisao_bow"] = r_tree

    OUT_PATH.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresultados salvos em {OUT_PATH}")


if __name__ == "__main__":
    main()
