# Item 6 — As técnicas estudadas auxiliam o projeto da Unidade 1?

> **Uso de IA generativa**: esta análise foi redigida com apoio do **Claude
> (Anthropic, Sonnet 4.5/5)** via Claude Code, a partir de prompt pedindo uma
> síntese argumentativa ligando os achados dos itens 2 a 5 ao projeto da
> Unidade 1 (Family History Search — busca de sobrenome em jornais
> digitalizados da Hemeroteca Digital Brasileira, com extração de pessoas,
> datas, eventos e relações, pra apoiar pesquisa genealógica).

O projeto da Unidade 1 tem um objetivo concreto: dado um sobrenome, achar
menções em jornais antigos e organizar isso em algo que ajude alguém a
reconstruir uma árvore genealógica — sem nunca afirmar parentesco sem
evidência. As técnicas de PLN estudadas nas Unidades 2 e 3 ajudam esse
objetivo de formas bem diferentes entre si, e os resultados reais obtidos
mostram exatamente onde cada uma ajuda e onde não ajuda.

**Busca e recomendação (item 3) é a que mais se encaixa diretamente.** O
próprio projeto já tem uma tela de busca por sobrenome — adicionar busca
semântica (TF-IDF pra precisão lexical, BERT pra tolerância a paráfrase/OCR)
sobre os trechos já coletados é uma extensão natural: alguém pesquisando a
família poderia digitar "casamento" ou "falecimento" em vez de só o
sobrenome, e navegar direto pros documentos mais relevantes — os resultados
do item 3 mostram que isso funciona bem quando combinado (lexical +
semântico), exatamente a proposta de melhoria que fizemos lá.

**NER e Extração de Relações (item 5) *já são* o projeto da Unidade 1** —
não é uma técnica nova que "auxiliaria", é o núcleo do que o pipeline já
faz (`src/extraction/`). Os números reais (631 pessoas, 610 relações
extraídas de 253 ocorrências) mostram que essa parte é produtiva e já
entrega valor genealógico direto: cada relação `filho_de` ou `residente_em`
extraída é um fiapo de evidência pra reconstrução de árvore familiar, com
link pra fonte — exatamente o que o projeto promete.

**Clusterização (item 2) e análise de sentimento (item 5) ajudariam menos
diretamente, e os resultados honestos mostram por quê.** A clusterização não
achou categorias temáticas reais no corpus de 253 documentos — os clusters
de maior qualidade métrica eram artefatos (tamanho de texto, duplicação de
anúncio), não "tipos de evento genealógico". Pra ser útil ao projeto, a
clusterização precisaria de um corpus bem maior (juntando as ocorrências de
vários sobrenomes já coletados) e provavelmente de uma representação com
ajuste fino, não um Word2Vec treinado do zero em 5 mil tokens. A análise de
sentimento teve um resultado lateral genuinamente útil — expôs um caso real
em que o campo `column_mix_suspected` do projeto não capturou uma mistura de
colunas que deveria ter sinalizado — mas como ferramenta genealógica
principal, "este trecho é positivo ou negativo" não ajuda diretamente a
montar uma árvore familiar; é mais um detector de qualidade de dado do que
uma feature do produto final.

**Classificação (item 4) é a que menos ajuda, e por um motivo que é, em si,
uma lição sobre o projeto da Unidade 1.** Não foi possível treinar um
classificador confiável porque o próprio projeto ainda não tem rótulos
suficientes — o `event_type` que ele já produz é fraco (baseado em regras
simples, 248 de 253 documentos caem em "outro"). Isso não é uma falha das
técnicas de classificação; é evidência de que o classificador de evento da
Unidade 1 precisaria ser repensado (ou treinado com uma amostra rotulada bem
maior) antes de qualquer tarefa supervisionada conseguir aproveitá-lo.

**Em resumo**: das quatro frentes estudadas, duas (busca semântica, que já
existe como NER/RE) auxiliam diretamente e de forma mensurável o projeto da
Unidade 1; uma (sentimento) auxilia indiretamente, como ferramenta de
auditoria de qualidade de dado; e uma (clusterização) e a classificação
expuseram limitações reais do dataset atual (corpus pequeno, rótulos
escassos) que precisariam ser resolvidas — ampliando a coleta pra mais
sobrenomes, e investindo em rotulagem manual de uma amostra — antes de essas
duas últimas técnicas gerarem valor prático pro projeto.
