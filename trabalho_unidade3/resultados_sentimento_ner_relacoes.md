# Item 5 — Análise de sentimento, NER e Extração de Relações

> **Uso de IA generativa**: código (`sentimento_ner_relacoes.py`) e esta
> análise desenvolvidos com apoio do **Claude (Anthropic, Sonnet 4.5/5)** via
> Claude Code, a partir de prompt pedindo a aplicação de análise de
> sentimento sobre o corpus e o levantamento do que já existia de NER/Extração
> de Relações no projeto da Unidade 1.

## Análise de sentimento — aplicável aos dados? Que resultado foi obtido?

**Sim, é aplicável**, com ressalvas. Aplicamos
`nlptown/bert-base-multilingual-uncased-sentiment` (modelo pré-treinado,
multilíngue, escala de 1 a 5 estrelas) aos 253 `context_excerpt`, mapeando
1–2★ → negativo, 3★ → neutro, 4–5★ → positivo (nenhum treino novo, só
inferência — técnica diferente das já usadas nos itens 2/3, que eram de
representação/busca, não de classificação de tom).

**Distribuição no corpus inteiro**: 94 documentos positivos, 70 neutros, 89
negativos — um corpus de recortes de jornal nada é uniformemente "neutro",
há bastante conteúdo realmente negativo (notícias policiais, carteiras
extraviadas, avisos de óbito) e bastante positivo (aniversários, casamentos,
elogios em colunas sociais).

**Validação com os 5 documentos de `event_type` conhecido** (o teste mais
honesto que temos, por menor que seja a amostra):

| event_type | estrelas | polaridade | texto |
|---|---|---|---|
| obito | 1★ | negativo | "DE FALECIMENTO DE MARCIJS VINÍCIUS ULIANQ EFFTING..." |
| obito | 1★ | negativo | "...responsável pela morte de Newton Michael Kovalenko..." |
| casamento | **1★** | **negativo** | "Circulando os convites para o casamento de Patrícia Uliano Effting e Cláudio Moura. Será dia 30. **Sepultado aqui**, quarta-feira de manhã, o sr. Tomé Inácio" |
| nascimento (falso positivo, ver item 4/item 2) | 3★ | neutro | lista de nomes |
| nascimento (falso positivo) | 3★ | neutro | lista de nomes |

Os dois óbitos genuínos foram classificados corretamente como negativos — o
modelo capturou bem o tom fúnebre. As duas menções de "nascimento" (que já
sabíamos, de análises anteriores, serem falsos positivos do classificador de
`event_type` — são listas de nomes, não anúncios de nascimento) saíram
neutras, coerente com o conteúdo real.

O caso do **casamento é o achado mais interessante deste item**: o modelo
classificou como **negativo**, o oposto do esperado para um anúncio de
casamento. Mas olhando o texto completo do `context_excerpt`, a razão fica
clara: ele mistura duas notícias que estavam fisicamente próximas na página
do jornal — o anúncio de casamento **e o início de uma notícia de
sepultamento não relacionada** ("...Será dia 30. Sepultado aqui,
quarta-feira de manhã, o sr. Tomé Inácio"). O projeto já tem um campo
(`column_mix_suspected`) pra sinalizar exatamente esse tipo de mistura de
colunas no recorte de contexto — mas, verificando, **esse campo está `False`
pra esse documento específico**: é um caso real de mistura que o heurístico
existente não capturou. A análise de sentimento, aqui, serviu
indiretamente como um teste de qualidade adicional pra um problema já
conhecido do pipeline de pré-processamento da Unidade 1.

## NER e Extração de Relações — aplicável? Que resultado foi obtido?

**Sim, e já estava implementado desde a Unidade 1** (`src/extraction/entities.py`
e `src/extraction/relations.py`) — não precisou de código novo, só
reaproveitamos a extração já rodada sobre o mesmo corpus de 253 ocorrências.
Números reais do dataset (`occurrences_sc_effting_extracted.jsonl`):

- **NER**: 631 menções de pessoas reconhecidas no total (média de 2,49 por
  documento); 48 dos 253 documentos (~19%) não tiveram nenhuma pessoa
  reconhecida — tipicamente os recortes mais curtos ou mais corrompidos
  pelo OCR.
- **Extração de relações**: 610 relações extraídas, distribuídas assim:

| tipo de relação | quantidade |
|---|---|
| filho_de | 463 |
| residente_em | 132 |
| faleceu_em | 11 |
| viuvo_de | 3 |
| casou_com | 1 |

O predomínio de `filho_de` reflete o próprio gênero textual do corpus: notas
sociais e necrológicas brasileiras quase sempre mencionam filiação ("fulano,
filho de ciclano e beltrana..."), então é o padrão que mais aparece em texto
corrido de jornal — faz sentido que o extrator baseado em regras encontre
muito mais disso do que, por exemplo, `casou_com` (que só aparece quando o
texto usa literalmente essa construção, rara mesmo nos documentos realmente
sobre casamento).

**Achado relevante**: o volume de NER e extração de relações (631 pessoas,
610 relações) é ordens de grandeza maior do que a classificação de
`event_type` sugere (só 5 documentos "rotulados"). Isso confirma que a
classificação de evento (item 4) é um sinal muito mais pobre do que a
extração estruturada de entidades e relações — o projeto da Unidade 1
produz dado genealogicamente útil mesmo quando não consegue dizer "que tipo
de evento é este", o que é relevante pra responder o item 6.

## Reprodutibilidade

```powershell
python -m trabalho_unidade3.sentimento_ner_relacoes
```

Resultados completos (amostra de 30 documentos com sentimento detalhado, +
todos os exemplos de pessoas/relações) em
`resultados_sentimento_ner_relacoes.json`.
