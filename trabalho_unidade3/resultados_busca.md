# Item 3 — Busca e recomendação

> **Uso de IA generativa**: este trabalho (código em `corpus.py`,
> `word2vec_simples.py`, `representacoes.py`, `busca_recomendacao.py`, e a
> redação desta análise) foi desenvolvido com apoio do **Claude (Anthropic,
> modelo Sonnet 4.5/5)**, via Claude Code, a partir de prompts descrevendo o
> enunciado do trabalho e pedindo a implementação das quatro representações,
> a execução das buscas sobre os dados reais da Unidade 1, e a análise
> crítica dos resultados obtidos. Todos os números, tabelas e trechos citados
> nesta análise são saída direta e verificada do código executado — nenhum
> resultado foi inventado ou ajustado manualmente.

## Metodologia

**Corpus**: os 253 trechos de contexto (`context_excerpt`) extraídos na
Unidade 1 ao buscar o sobrenome "Effting" em jornais catarinenses digitalizados
(Hemeroteca Digital Brasileira). Cada documento é um recorte curto (24–985
caracteres, média de 129) ao redor da menção ao sobrenome numa página de
jornal — texto com ruído típico de OCR de jornal antigo (acentos perdidos
viram `�`, erros de reconhecimento de caractere como "ULIANQ" em vez de
"ULIANO").

**Representações construídas** (código em `representacoes.py`):

- **BoW**: `CountVectorizer` do scikit-learn, contagem bruta de termos.
- **TF-IDF**: `TfidfVectorizer` do scikit-learn.
- **Word2Vec**: skip-gram com negative sampling, **implementado do zero em
  numpy** (`word2vec_simples.py`) — o gensim não tem wheel pré-compilada para
  Python 3.14 no Windows deste ambiente (a extensão `word2vec_inner` exige
  compilador C++, indisponível aqui). Treinado do zero só com o corpus deste
  trabalho (253 documentos, ~5.100 tokens, vocabulário de 2.245 palavras),
  50 dimensões, janela 4, 40 épocas. Vetor de documento = média dos vetores
  das palavras que o compõem.
- **BERT**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (pré-treinado, multilíngue, **não** re-treinado no nosso corpus — usado só
  para inferência).

**Busca**: pra cada consulta, calcula-se a similaridade de cosseno entre o
vetor da consulta e o vetor de cada documento, na mesma representação, e
ordena-se decrescente. Top-5 completo por consulta/representação está salvo
em `resultados_busca.json`; aqui vão as tabelas resumidas (as 3 primeiras
linhas de cada tabela já respondem o "top-3" pedido).

**Consultas**: escolhidas pra cobrir ângulos diferentes do corpus —
casamento, óbito, aniversário/coluna social, e uma consulta "de controle"
com só o sobrenome (que está presente em 100% dos documentos, por construção
da coleta da Unidade 1).

## Resultados por consulta

### "casamento convite núpcias"

Existe exatamente 1 documento rotulado `casamento` no corpus inteiro: *"Circulando
os convites para o casamento de Patrícia Uliano Effting e Cláudio Moura. Será
dia 30."*

| # | BoW | TF-IDF | Word2Vec | BERT |
|---|-----|--------|----------|------|
| 1 | **0.183 — doc do casamento** | **0.252 — doc do casamento** | 0.862 — "...dra. Patrícia Uliano Effting, q..." (candidatura a tribunal) | **0.553 — doc do casamento** |
| 2 | 0.000 — "Noite Black Tie" | 0.000 — "Noite Black Tie" | **0.845 — doc do casamento** | 0.516 — "ao lado do esposo, JoSo Ben-..." |
| 3 | 0.000 — "Brinca Quem Pode" | 0.000 — "Brinca Quem Pode" | 0.830 — "...editora-chefe de A Tribuna..." | 0.456 — "Padre celebrante: Pe. Léo Effting..." |

BoW e TF-IDF acertam o documento certo em 1º lugar — mas caem pra similaridade
**zero** já no 2º colocado: a partir do momento em que não há mais nenhuma
palavra do vocabulário em comum com a consulta, a similaridade de cosseno
esparsa simplesmente zera, não existe noção de "quase relacionado". Word2Vec
erra o 1º lugar (traz uma matéria sobre uma candidatura a tribunal, só porque
cita a mesma pessoa, Patrícia Uliano Effting, em outro contexto completamente
diferente) — sintoma de um modelo treinado do zero num corpus pequeno demais
pra aprender uma noção de "casamento" separada de "Patrícia" como entidade.
O BERT foi o único a acertar o 1º lugar com uma margem de score relativamente
alta e manter 2º/3º lugares tematicamente plausíveis (menções a cônjuge, padre
celebrante) mesmo sem nenhuma sobreposição literal de palavras com a consulta.

### "falecimento sepultamento morte"

Existem 2 documentos rotulados `obito`: o aviso de falecimento de "MARCIJS
VINÍCIUS ULIANQ EFFTING" e uma notícia policial envolvendo a "morte de Newton
Michael Kovalenko", que cita um casal Effting como responsável.

| # | BoW | TF-IDF | Word2Vec | BERT |
|---|-----|--------|----------|------|
| 1 | **0.224 — óbito 1** | **0.317 — óbito 1** | **0.852 — óbito 1** | 0.439 — doc do casamento (!) |
| 2 | **0.080 — óbito 2** | **0.097 — óbito 2** | 0.789 — "Marcus Vinícius Uliano Effting, 30 anos..." | 0.435 — doc sobre "socorros diversos" |
| 3 | 0.000 — "Brinca Quem Pode" | 0.000 — "Brinca Quem Pode" | 0.786 — "Laguna, 29 de janeiro de 2003. Dionísio Effting..." | **0.403 — óbito 2** |

Essa é a consulta em que BoW, TF-IDF e Word2Vec tiveram o melhor desempenho do
trabalho inteiro: os dois únicos documentos de óbito do corpus inteiro saem
em 1º e 2º lugar nas três representações baseadas em termos/co-ocorrência, e
o Word2Vec ainda traz, em 2º e 3º, dois outros trechos que também falam da
mesma pessoa falecida (mesmo sem o rótulo `obito` — evidência de que a
classificação de `event_type` do projeto, baseada em regras simples, deixa
passar casos reais). O BERT, ao contrário, erra feio: coloca o documento do
**casamento** em 1º lugar e só acha o segundo óbito real em 3º. Hipótese mais
provável: o modelo pré-treinado de sentence embeddings está captando
semelhança de **registro/estilo** (nota social formal de jornal) mais do que
o conteúdo semântico específico "morte" vs. "casamento" — sem ajuste fino
(fine-tuning) pro domínio, ele trata textos curtos e ruidosos de coluna social
como pertencentes à mesma "vizinhança" semântica.

### "aniversariante parabéns"

Não existe nenhum documento rotulado como evento de aniversário no corpus
(não é uma categoria do `event_type` do projeto), então aqui a avaliação é só
qualitativa.

| # | BoW | TF-IDF | Word2Vec | BERT |
|---|-----|--------|----------|------|
| 1 | "Parabéns! IRMÃ ULIANO EFFTING ZOCH..." | mesmo doc | mesmo doc | mesmo doc |
| 2 | 0.000 | 0.000 | "...Nice Gasparoto Effting, 36 anos..." | "...click especial para a coluna..." |
| 3 | 0.000 | 0.000 | "...ao lado do esposo, JoSo Ben-..." | "A família de MARCUS VINÍCIUS..." |

As quatro representações concordam no 1º lugar — é o único documento do
corpus que contém literalmente a palavra "Parabéns", então é um caso "fácil"
em que lexical e semântico convergem. A diferença aparece a partir do 2º
lugar: BoW/TF-IDF zeram (nenhum outro documento compartilha "aniversariante"
ou "parabéns"), enquanto Word2Vec e BERT continuam trazendo notas sociais
plausíveis (ainda que nenhuma seja comprovadamente sobre aniversário).

### "Effting" (consulta de controle, só o sobrenome)

Como o sobrenome está presente em 100% dos 253 documentos (foi o critério de
coleta da Unidade 1), essa consulta mede o que cada representação faz quando
o termo buscado não discrimina nada.

| # | BoW | TF-IDF | Word2Vec | BERT |
|---|-----|--------|----------|------|
| 1 | 0.555 | 0.175 | 0.686 | 0.545 — "DRESCH, EFFTING, E\|-IAS," (lista de sobrenomes) |
| 2 | 0.500 — "DRESCH, EFFTING..." | 0.118 — "DRESCH, EFFTING..." | 0.672 — "DRESCH, EFFTING..." | 0.478 — óbito 1 |
| 3 | 0.471 | 0.113 | 0.652 (rotulado `nascimento`) | 0.442 |

Os quatro resultados de topo divergem bastante entre representações — sem
surpresa, já que não há nenhum termo discriminante na consulta. Achado
relevante: o 3º lugar do Word2Vec tem `event_type = nascimento`, mas o texto
("neiro Ferreira, Uliano Effting, Nascimento, Marcelo") é claramente uma lista
de nomes em que "Nascimento" é o **sobrenome de outra pessoa**, não um evento
de nascimento — confirma, usando a própria busca, um problema de qualidade
já documentado no projeto (classificador de `event_type` baseado em regras
simples gera falsos positivos).

## Os resultados são utilizáveis em um contexto real?

Depende do representante e do tipo de consulta:

- **BoW/TF-IDF são utilizáveis quando a consulta usa vocabulário que
  realmente aparece no corpus** (ex.: "casamento", "falecimento") — nesses
  casos foram tão bons quanto ou melhores que Word2Vec/BERT no 1º lugar, com
  a vantagem de serem interpretáveis (dá pra saber exatamente por que um
  documento pontuou: quais palavras bateram). O problema real pra uso em
  produção é a saturação em zero: qualquer consulta cujas palavras não
  aparecem literalmente no corpus não retorna nada útil, o que é grave pra
  um corpus de só 253 documentos curtos (o vocabulário é pequeno e esparso).
- **Word2Vec treinado do zero neste corpus não está pronto pra uso real.**
  Mistura entidade (pessoa) com tópico (evento) por não ter dados suficientes
  pra separar os dois — ficou claro na consulta de casamento, em que o 1º
  lugar foi sobre a mesma pessoa mas em contexto totalmente diferente
  (candidatura a tribunal). 253 documentos (~5.100 tokens) é pouco para
  skip-gram aprender uma geometria semântica confiável; a literatura
  costuma recomendar corpora de pelo menos centenas de milhares de tokens.
- **BERT pré-treinado é o mais forte "em média"**, mas não é confiável sem
  supervisão: errou de forma notável justamente na consulta mais importante
  pro caso de uso genealógico (óbito vs. casamento), que é exatamente o tipo
  de distinção que mais importa pra quem está reconstruindo uma árvore
  familiar. Não dá pra confiar cegamente no top-1 do BERT sem revisão
  humana — o que, aliás, é consistente com o resto do projeto: a interface
  (Streamlit) já trata toda extração como hipótese revisável, nunca como
  fato confirmado automaticamente.

## Como poderiam ser combinados ou melhorados

1. **Busca híbrida (lexical + semântica)**: combinar o score de TF-IDF com o
   de BERT (ex.: média ponderada, ou usar TF-IDF pra um primeiro corte e BERT
   só pra re-ranquear os top-20) — pega a precisão de BoW/TF-IDF quando o
   vocabulário bate, e a tolerância a paráfrase/sinônimo do BERT quando não
   bate. É a abordagem padrão de produção (busca "híbrida" ou retrieval com
   re-ranking) por um motivo.
2. **Usar o `event_type` já extraído como filtro, não só como rótulo
   passivo**: já que a extração por regras da Unidade 1 gera `event_type`
   (ainda que com falsos positivos, como mostrado acima), ele poderia
   restringir a busca antes de rankear (ex.: consulta sobre óbito já filtra
   pra `event_type in {obito, outro}` excluindo `casamento` explicitamente) —
   reduziria o tipo de erro que o BERT cometeu na consulta de óbito.
3. **BERT com fine-tuning de domínio**, ou pelo menos um modelo de sentence
   embeddings treinado/calibrado em português histórico/jornalístico, em vez
   de um modelo multilíngue genérico — o erro observado (misturar registro
   formal de nota social com conteúdo semântico) é exatamente o tipo de coisa
   que fine-tuning supervisionado corrige.
4. **Corpus maior pro Word2Vec**: juntar o `context_excerpt` de buscas por
   outros sobrenomes já coletadas no projeto (não só "Effting") daria um
   corpus ordens de magnitude maior, permitindo embeddings genuinamente
   semânticos em vez de memorização de co-ocorrência de nomes próprios.
5. **Correção/normalização do ruído de OCR antes de vetorizar** — o
   substituição de `�` e a correção de erros óbvios de caractere (ex.:
   "ULIANQ" → "ULIANO") antes de tokenizar reduziriam o tamanho artificial do
   vocabulário de BoW/TF-IDF e melhorariam a qualidade dos embeddings
   treinados, já que atualmente cada variante corrompida de uma mesma palavra
   vira um token diferente.

## Reprodutibilidade

```powershell
python -m trabalho_unidade3.busca_recomendacao
```

Gera `resultados_busca.json` com o top-5 completo de cada consulta × cada
representação (esta análise usa um subconjunto formatado em tabela). Código
das representações em `representacoes.py`, do Word2Vec do zero em
`word2vec_simples.py`, do carregamento/tokenização do corpus em `corpus.py`.
