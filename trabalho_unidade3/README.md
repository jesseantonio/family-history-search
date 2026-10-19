# Trabalho da Unidade 3 — Processamento de Linguagem Natural

PLN 2026/2 — Universidade Regional de Blumenau (FURB), Ciência da
Computação. Dados: as 253 ocorrências do sobrenome "Effting" coletadas na
Unidade 1 (projeto *Family History Search*, na raiz deste repositório),
usando o `context_excerpt` de cada uma como corpus textual.

Representações construídas (`representacoes.py`): **BoW** e **TF-IDF**
(scikit-learn), **Word2Vec** (skip-gram com negative sampling, implementado
do zero em `word2vec_simples.py` — gensim não compila nesse Python
3.14/Windows sem Build Tools do C++), e **BERT** pré-treinado
(`paraphrase-multilingual-MiniLM-L12-v2`, via sentence-transformers).

## 1) Atividades realizadas

| | BoW | TF-IDF | Word2Vec | BERT |
|---|---|---|---|---|
| Clusterização + PCA e visualização | ✔ | ✔ | ✔ | ✔ |
| Busca e recomendação | ✔ | ✔ | ✔ | ✔ |
| Classificação | ✔* | ✔* | ✔* | ✔* |

\* Classificador treinado e avaliado (leave-one-out), mas **não confiável**
para as classes minoritárias — ver `resultados_classificacao.md`, que
explica e demonstra por quê (só 5 exemplos rotulados fora da classe
majoritária, em todo o corpus).

- Análise de sentimento: ( x ) Foi realizada
- Reconhecimento de Entidades Nomeadas: ( x ) Foi realizado — reaproveitado
  da Unidade 1, não reimplementado
- Extração de Relações: ( x ) Foi realizada — reaproveitado da Unidade 1,
  não reimplementado

## 2–6) Respostas

| item | arquivo | o que responde |
|---|---|---|
| 2 | [`resultados_clusterizacao.md`](resultados_clusterizacao.md) | clusterização, qual representação foi mais representativa, k ideal, métricas intra/inter-cluster, imagens em `figuras/` |
| 3 | [`resultados_busca.md`](resultados_busca.md) | busca e recomendação, qualidade do top-3/top-5, uso em contexto real, como melhorar |
| 4 | [`resultados_classificacao.md`](resultados_classificacao.md) | se foi possível classificar, comportamento em dados novos, comparação entre representações, técnicas de modelo de linguagem/decisão |
| 5 | [`resultados_sentimento_ner_relacoes.md`](resultados_sentimento_ner_relacoes.md) | sentimento, NER, extração de relações — aplicabilidade e resultados |
| 6 | [`conclusao.md`](conclusao.md) | as técnicas auxiliam o projeto da Unidade 1? |

## Reprodutibilidade

```powershell
pip install -r trabalho_unidade3/requirements.txt  # além do requirements.txt da raiz
python -m trabalho_unidade3.busca_recomendacao
python -m trabalho_unidade3.clusterizacao_pca
python -m trabalho_unidade3.classificacao
python -m trabalho_unidade3.sentimento_ner_relacoes
```

Cada script salva seus resultados completos em `resultados_*.json` — os
`.md` citam números e exemplos reais extraídos desses JSONs, nada foi
inventado manualmente.

## Uso de IA generativa (declaração geral)

Todo o código e toda a redação deste trabalho (itens 2 a 6) foram
desenvolvidos com apoio do **Claude (Anthropic, modelo Sonnet 4.5/5)**, via
Claude Code, a partir de prompts descrevendo o enunciado da Unidade 3 e
pedindo a implementação de cada técnica sobre os dados reais já coletados
na Unidade 1, seguida da execução real dos experimentos e da análise crítica
dos resultados obtidos — nenhum número, tabela ou exemplo citado nos
arquivos `resultados_*.md` foi inventado; todos são saída direta e
verificável do código em `resultados_*.json`.
