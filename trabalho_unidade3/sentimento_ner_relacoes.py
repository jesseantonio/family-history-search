"""Item 5 do trabalho: análise de sentimento (novo, feito aqui) + um
levantamento do que já existe de Reconhecimento de Entidades Nomeadas (NER)
e Extração de Relações, reaproveitando a extração real já feita na
Unidade 1 (`src/extraction/`), sem reinventar o que já está pronto.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from trabalho_unidade3.corpus import carregar_corpus

DATASET_EXTRAIDO = Path(__file__).resolve().parents[1] / "data" / "processed" / "occurrences_sc_effting_extracted.jsonl"
OUT_PATH = Path(__file__).resolve().parent / "resultados_sentimento_ner_relacoes.json"


def analisar_sentimento(textos: list[str]) -> list[dict]:
    from transformers import pipeline

    classificador = pipeline(
        "sentiment-analysis",
        model="nlptown/bert-base-multilingual-uncased-sentiment",
    )
    saidas = classificador(textos, truncation=True)

    resultados = []
    for texto, saida in zip(textos, saidas):
        estrelas = int(saida["label"][0])  # rótulo tipo "4 stars" -> 4
        if estrelas <= 2:
            polaridade = "negativo"
        elif estrelas == 3:
            polaridade = "neutro"
        else:
            polaridade = "positivo"
        resultados.append({
            "texto": texto,
            "estrelas": estrelas,
            "confianca": float(saida["score"]),
            "polaridade": polaridade,
        })
    return resultados


def levantar_ner_relacoes() -> dict:
    """Reaproveita a extração já feita na Unidade 1 — não reimplementa NER/RE
    do zero, só soma/organiza o que `src/extraction/` já produziu sobre o
    mesmo corpus."""
    pessoas_por_doc = []
    lugares_por_doc = []
    relacoes_total = []
    exemplos_relacoes = []
    exemplos_pessoas = []

    with open(DATASET_EXTRAIDO, encoding="utf-8") as f:
        for linha in f:
            rec = json.loads(linha)
            pessoas = rec.get("people_mentioned", []) or []
            lugares = rec.get("places_mentioned", []) or []
            relacoes = rec.get("relations", []) or []

            pessoas_por_doc.append(len(pessoas))
            lugares_por_doc.append(len(lugares))
            relacoes_total.extend(relacoes)

            if relacoes and len(exemplos_relacoes) < 8:
                exemplos_relacoes.append({
                    "contexto": rec.get("context_excerpt", "")[:150],
                    "relacoes": relacoes,
                })
            if pessoas and len(exemplos_pessoas) < 5:
                exemplos_pessoas.append({
                    "contexto": rec.get("context_excerpt", "")[:150],
                    "pessoas": [p.get("name_raw") for p in pessoas],
                })

    tipos_relacao = Counter(r.get("relation", "?") for r in relacoes_total)

    return {
        "n_documentos": len(pessoas_por_doc),
        "total_pessoas_mencionadas": sum(pessoas_por_doc),
        "media_pessoas_por_doc": sum(pessoas_por_doc) / len(pessoas_por_doc) if pessoas_por_doc else 0,
        "docs_sem_nenhuma_pessoa": sum(1 for n in pessoas_por_doc if n == 0),
        "total_lugares_mencionados": sum(lugares_por_doc),
        "total_relacoes_extraidas": len(relacoes_total),
        "tipos_de_relacao": dict(tipos_relacao),
        "exemplos_relacoes": exemplos_relacoes,
        "exemplos_pessoas": exemplos_pessoas,
    }


def main() -> None:
    docs = carregar_corpus()
    textos = [d.texto for d in docs]
    print(f"{len(docs)} documentos carregados")

    print("\n=== Análise de sentimento (novo) ===")
    print("carregando modelo de sentimento (nlptown/bert-base-multilingual-uncased-sentiment)...")
    sentimentos = analisar_sentimento(textos)
    distrib_polaridade = Counter(s["polaridade"] for s in sentimentos)
    distrib_estrelas = Counter(s["estrelas"] for s in sentimentos)
    print("distribuição de polaridade:", dict(distrib_polaridade))
    print("distribuição de estrelas (1-5):", dict(sorted(distrib_estrelas.items())))

    # Sanity-check: como o modelo classifica os 5 documentos com event_type
    # conhecido (óbito deveria pender negativo, casamento positivo)?
    print("\nsentimento nos 5 documentos com event_type != 'outro':")
    sent_por_id = {t: s for t, s in zip(textos, sentimentos)}
    for d in docs:
        if d.event_type != "outro":
            s = sent_por_id[d.texto]
            print(f"  [{d.event_type:10s}] {s['estrelas']} estrelas ({s['polaridade']:9s}) - {d.texto[:80]}")

    print("\n=== NER e Extração de Relações (reaproveitado da Unidade 1) ===")
    ner_re = levantar_ner_relacoes()
    print(f"pessoas mencionadas: {ner_re['total_pessoas_mencionadas']} (média {ner_re['media_pessoas_por_doc']:.2f}/doc)")
    print(f"documentos sem nenhuma pessoa reconhecida: {ner_re['docs_sem_nenhuma_pessoa']}")
    print(f"relações extraídas: {ner_re['total_relacoes_extraidas']}  tipos: {ner_re['tipos_de_relacao']}")

    resultados = {
        "sentimento": {
            "distribuicao_polaridade": dict(distrib_polaridade),
            "distribuicao_estrelas": {str(k): v for k, v in distrib_estrelas.items()},
            "exemplos_com_event_type_conhecido": [
                {
                    "event_type": d.event_type,
                    "texto": d.texto,
                    **sent_por_id[d.texto],
                }
                for d in docs if d.event_type != "outro"
            ],
            "amostra_detalhada": sentimentos[:30],
        },
        "ner_relacoes": ner_re,
    }
    OUT_PATH.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresultados salvos em {OUT_PATH}")


if __name__ == "__main__":
    main()
