# Item 4 — Classificação

> **Uso de IA generativa**: código (`classificacao.py`) e esta análise
> desenvolvidos com apoio do **Claude (Anthropic, Sonnet 4.5/5)** via Claude
> Code, a partir de prompt pedindo a tentativa de classificação supervisionada
> sobre as representações já construídas, com avaliação honesta mesmo diante
> da escassez de rótulos.

## O rótulo disponível e por que ele é um problema

O único rótulo existente no dataset é o `event_type`, extraído por regras na
Unidade 1. Distribuição real no corpus de 253 documentos:

| classe | n |
|---|---|
| outro | 248 |
| nascimento | 2 |
| obito | 2 |
| casamento | **1** |

Com a classe `casamento` tendo um único exemplo em todo o corpus, uma
divisão treino/teste tradicional é impossível de fazer de forma válida — ou
o único exemplo fica no treino (e o teste nunca vê essa classe) ou fica no
teste (e o treino nunca aprende essa classe). Em vez de esconder esse
problema com uma divisão artificial, usamos **leave-one-out (LOO)**: cada um
dos 253 documentos é testado individualmente, com o modelo treinado nos
outros 252 — é a validação mais generosa possível com dados tão escassos.

## 1) Foi possível treinar um classificador?

Tecnicamente sim — o código roda, produz um modelo e gera previsões. **Mas
não um classificador confiável para as classes minoritárias.** Resultado
real do LOO com Regressão Logística (`class_weight="balanced"`, que já
tenta compensar o desbalanceamento) em cada representação:

| representação | acurácia | macro-F1 | casamento (P/R) | nascimento (P/R) | obito (P/R) | outro (P/R) |
|---|---|---|---|---|---|---|
| BoW | 0.976 | 0.247 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.98 / 1.00 |
| TF-IDF | 0.980 | 0.248 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.98 / 1.00 |
| Word2Vec | 0.763 | 0.216 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.97 / 0.78 |
| BERT | 0.941 | 0.242 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.98 / 0.96 |

Esse é o resultado clássico de classificação com classes extremamente
desbalanceadas: **a acurácia parece ótima (76 a 98%) só porque "outro" é
quase todo o corpus** — um classificador que sempre responde "outro" já
acerta ~98%. O macro-F1 (que dá peso igual a cada classe) mostra o quadro
real: ~0,22 a 0,25, porque nenhuma das três classes minoritárias foi
acertada **nem uma vez**, em nenhuma representação, sob Regressão Logística.

## 2) Como ele se comportou a partir de dados novos?

O LOO já simula exatamente isso: cada previsão é feita sobre um documento
que o modelo nunca viu durante aquele treino. O comportamento observado:
colapso total para a classe majoritária sempre que o documento de teste
pertence a uma classe rara. Isso é esperado matematicamente, não é um "bug"
— quando o único exemplo de `casamento` é o item de teste, sobra **zero**
exemplo de `casamento` no conjunto de treino daquela rodada, então nenhum
classificador, de nenhuma técnica, consegue prever essa classe
corretamente ali. O mesmo vale (com um pouco mais de folga) para
`nascimento` e `obito`, que têm só 2 exemplos cada.

## 3) Alguma representação favorece classificações mais precisas?

Sob Regressão Logística, as quatro representações tiveram desempenho
igualmente ruim nas classes minoritárias (0% de revocação em todas) — a
diferença entre elas (macro-F1 de 0,216 a 0,248) é pequena demais, e inteira
explicada pela classe majoritária, pra dizer que alguma representação
"ajuda" a classificar. **O fator limitante aqui não é a representação, é a
quantidade de dados rotulados.** Nenhuma representação textual compensa ter
1 exemplo de uma classe.

A única técnica que conseguiu acertar algo numa classe minoritária foi a
**árvore de decisão sobre BoW** (macro-F1 0,413, melhor que qualquer
Regressão Logística): acertou 1 dos 2 exemplos de `nascimento` (revocação
0,50). Ainda assim, errou os 2 exemplos de `obito` e o único de `casamento`
(confusão: o exemplo de `casamento` foi previsto como `obito`). O ganho
vem de como árvores de decisão particionam o espaço por regras discretas de
feature única — conseguem "decorar" um exemplo raro se ele tiver uma palavra
suficientemente distintiva — mas isso é mais memorização do que
generalização, dado o tamanho da amostra.

## 4) Técnica com modelo de linguagem ou modelo de decisão?

Sim, as duas, conforme pedido:

- **Modelo de linguagem**: embeddings do BERT pré-treinado
  (`paraphrase-multilingual-MiniLM-L12-v2`, o mesmo do item 3) como entrada
  de uma Regressão Logística — resultado na tabela acima (linha "BERT").
- **Modelo de decisão**: `DecisionTreeClassifier` do scikit-learn sobre BoW
  — resultado na seção anterior.

## Conclusão

**Não foi possível treinar um classificador confiável com os dados
disponíveis**, e a causa raiz está documentada com números reais acima: só
5 exemplos no total (1+2+2) cobrindo as 3 classes que não são "outro". Isso
bate com a justificativa que o próprio enunciado do trabalho já
exemplifica ("os dados não possuíam rótulos prontos para treinamento") — a
diferença é que aqui demonstramos isso empiricamente (LOO + métricas reais
por classe) em vez de só declarar a limitação. Pra viabilizar essa tarefa no
futuro, seria necessário ou (a) rotular manualmente uma amostra maior do
corpus de ocorrências já coletado (algumas centenas de exemplos por classe,
no mínimo), ou (b) usar uma técnica de poucos exemplos (few-shot) com um
modelo de linguagem maior, em vez de treinar um classificador do zero.

## Reprodutibilidade

```powershell
python -m trabalho_unidade3.classificacao
```
