# Family History Search

[![testes](https://github.com/jesseantonio/family-history-search/actions/workflows/tests.yml/badge.svg)](https://github.com/jesseantonio/family-history-search/actions/workflows/tests.yml)

Ferramenta de linha de comando para buscar um sobrenome no acervo de jornais e revistas da Hemeroteca Digital Brasileira (Biblioteca Nacional), transformar as ocorrências encontradas em eventos estruturados (pessoas, datas, lugares, relações) e apresentar tudo numa linha do tempo e num grafo de parentesco navegáveis.

O sobrenome é um parâmetro de entrada, não algo fixo no código — a ferramenta funciona para qualquer sobrenome. Os exemplos deste README usam `Effting` porque foi o caso usado para construir e validar o projeto, mas o mesmo fluxo serve para pesquisar qualquer outro nome de família.

> O sistema não afirma parentescos. Cada pessoa, evento e relação extraída carrega o trecho de evidência, o link para a página original e um nível de confiança. Agrupamentos de menções que podem ser a mesma pessoa são apresentados como hipóteses revisáveis, nunca como fatos confirmados automaticamente.

## O que o projeto faz

Dado um sobrenome, o pipeline:

1. busca ocorrências em periódicos da Hemeroteca Digital e baixa as páginas correspondentes;
2. extrai o texto de cada página (a Biblioteca Nacional já disponibiliza OCR embutido no PDF — não é necessário rodar OCR local) e recorta o trecho relevante ao redor do sobrenome, com sua posição na página;
3. reconhece pessoas, datas (completas, parciais ou relativas), lugares, tipo de evento (nascimento, casamento, óbito, outro) e relações explícitas (`filho_de`, `casou_com`, `viuvo_de`, `residente_em`, `faleceu_em`) em cada trecho;
4. agrupa, com uma pontuação explicável, quais menções provavelmente são a mesma pessoa;
5. mostra tudo numa interface web: uma linha do tempo filtrável e revisável, e uma árvore de parentesco navegável.

## Instalação

```powershell
pip install -r requirements.txt
```

Isso instala as dependências e o modelo de reconhecimento de entidades em português do spaCy. Se preferir instalar o modelo à parte: `python -m spacy download pt_core_news_sm`.

## Como usar

### Modo automático (recomendado)

A busca inteira — descobrir quais periódicos/edições de um estado têm o sobrenome, baixar, extrair e agrupar — roda com um único comando:

```powershell
python -m src.pipeline --surname Effting --uf SC
python -m src.pipeline --surname Effting --uf SC --ano-inicio 1900 --ano-fim 1950   # só um período
```

**Busca de uma pessoa** (em vez da família inteira): informe também o prenome.

```powershell
python -m src.pipeline --surname Barletta --nome Carlos --modo inteligente
python -m src.pipeline --surname Barletta --nome Carlos --modo literal
```

O memoria.bn só acha o termo exato, então a busca pesquisa o sobrenome lá e filtra cada página baixada pelo nome da pessoa (`src/resolution/people.py`, `find_person`):

- **literal** — o nome como digitado, sem diferença de acentos e maiúsculas: "Carlos Barletta" acha "CARLOS BARLETTA" e "João Carlos Barletta", mas não "Carlo Barleta" nem "Carlos Alberto Barletta";
- **inteligente** (padrão) — aceita erros de leitura do OCR e grafias ("Carlo Barleta", "CAETANO BXRLETTA") e nomes do meio ("Carlos Alberto Barletta"), mas não troca o gênero ("Carla"). Além do sobrenome digitado, pesquisa no memoria.bn até duas grafias prováveis dele ("Barletta" → também "Barleta"), por isso demora mais.

O resultado vai para `occurrences_pessoa_<nome-sobrenome>_*.jsonl`, separado da busca de família do mesmo sobrenome; os PDFs já baixados por ela são reaproveitados do cache. Cada ocorrência guarda como o nome foi lido naquela página (`match_text`), e o trecho é recortado em volta dele.

O comando abre o **Chrome instalado na máquina** (perfil próprio em `data/raw/.chrome_cdp_profile`, separado do seu perfil pessoal), conecta nele via CDP com o Playwright e faz a busca por Local/Período do `memoria.bn.gov.br` *de dentro da página* (`src/collector/browser_bn.py`). O DocReader, que pede CAPTCHA, nunca é aberto; se mesmo assim aparecer uma verificação, o script pausa e pede para você resolvê-la na janela do Chrome. Testamos ao vivo: um browser *lançado pelo Playwright* (headless ou headed) é bloqueado pela Cloudflare — por isso a ferramenta conecta num Chrome real aberto normalmente. Ver `ESTRATEGIA_BUSCA_SOBRENOME.md` para o histórico dessa decisão.

Etapas e arquivos gerados em `data/processed/`:

| Etapa | O que faz | Saída |
|---|---|---|
| 1. listar | edições com o sobrenome, segundo o memoria.bn | `listagem_<s>.csv` |
| 2. coletar | baixa os PDFs e acha as páginas com o sobrenome | `occurrences_region_<s>.jsonl` |
| 3. recortar | trecho em volta do nome | `..._enriched.jsonl` |
| 4. extrair | pessoas, datas, eventos, relações | `..._extracted.jsonl` |
| 5. agrupar | menções que podem ser a mesma pessoa | `person_clusters_region_<s>.json` |

Opções úteis:

- `--so-listar` para depois da etapa 1 (para revisar o CSV); `--da-listagem` pula a etapa 1 e usa o CSV já salvo.
- **Retomada:** se a execução for interrompida durante a etapa 2, rodar o mesmo comando de novo continua de onde parou (o progresso fica em `coleta_<s>.jsonl` até a etapa terminar). `--recomecar` ignora isso e refaz a etapa inteira. Os PDFs ficam em cache em `data/raw/<bib>/` e não são baixados de novo.
- Os ids das ocorrências são determinísticos (mesma página + mesmo sobrenome = mesmo id), então rodar a busca de novo **não** perde as revisões feitas na interface.

O mesmo fluxo está disponível em "Nova busca", na interface web (passo 7 abaixo). A busca roda em segundo plano: dá para recarregar a página sem perdê-la, e só uma busca roda por vez. O log completo fica em `data/processed/busca_<s>.log`.

As seções abaixo documentam as etapas manuais equivalentes, úteis para depurar uma etapa isoladamente ou para um periódico específico.

### Etapas manuais (uma região nova, ou depuração isolada)

O pipeline tem seis etapas, cada uma com sua própria linha de comando. A saída de uma etapa é a entrada da próxima.

### 1. Descobrir o `bib` de um periódico

Cada jornal/revista da Hemeroteca tem um identificador numérico (`bib`). Hoje isso só se descobre manualmente: procurar o periódico em [bndigital.bn.gov.br](https://bndigital.bn.gov.br) e copiar o número da URL (ex.: `.../jornal/886173`). Esse site está atrás de uma proteção anti-robô, então essa etapa não é automatizável — mas é rápida, alguns segundos por periódico.

### 2. Buscar o sobrenome dentro de um periódico

Duas formas, dependendo do que estiver disponível:

**A. Lista de ocorrências da própria Hemeroteca (mais rápido).** Depois de pesquisar o sobrenome no visualizador do periódico, salve o HTML da página de resultados (`ListaPastaOcor.aspx`, Ctrl+U no navegador) e rode:

```powershell
python -m src.collector --surname Effting --bib 886173 --hits-html caminho\lista_ocorrencias.html --titulo "Nome do Periódico"
```

Isso baixa só as edições que já sabidamente têm o sobrenome.

**B. Índice completo do periódico (quando não há lista pronta).** Salve o HTML de `bndigital.bn.gov.br/acervo-digital/jornal/<bib>` (Ctrl+U) e rode:

```powershell
python -m src.collector --surname Effting --bib 886173 --index-html caminho\indice.html --start-year 1900 --end-year 1913
```

Isso baixa e verifica todas as edições do intervalo — mais lento, mas não depende de uma busca prévia da Hemeroteca.

Os dois modos salvam o resultado em `data/processed/occurrences_<bib>_<sobrenome>.jsonl`.

### 3. Buscar em vários periódicos de uma vez (opcional)

`memoria.bn.gov.br/DocReader/docmulti.aspx` mostra, quando a busca é ordenada pela coluna "Ocorrências" em ordem decrescente, todos os periódicos de uma região que têm alguma ocorrência do termo, com a contagem exata. Com essa página capturada (HTML + cookie de sessão + um exemplo de requisição — ver o cabeçalho de `src/collector/search_region.py` para o passo a passo da captura), dá pra varrer automaticamente todos os periódicos com ocorrência de uma vez:

```powershell
python -m src.collector.search_region --surname Effting --ranked-grid grade_ordenada.html --sessao-id M50757006119502 --pesq "\"Effting\"" --cookie "cookie completo do navegador"
```

Isso já gera diretamente o `data/processed/occurrences_region_<sobrenome>.jsonl` com todos os periódicos, sem precisar repetir o passo 2 um por um.

### 4. Pré-processar (normalizar e recortar contexto)

```powershell
python -m src.preprocessing.segment --in data/processed/occurrences_886173_effting.jsonl --out data/processed/occurrences_886173_effting_enriched.jsonl
```

### 5. Extrair pessoas, datas, eventos e relações

```powershell
python -m src.extraction.pipeline --in data/processed/occurrences_886173_effting_enriched.jsonl --out data/processed/occurrences_886173_effting_extracted.jsonl
```

### 6. Agrupar menções da mesma pessoa (opcional, mas recomendado antes do grafo)

```powershell
python -m src.resolution.pipeline --in data/processed/occurrences_886173_effting_extracted.jsonl --surname Effting --out data/processed/person_clusters_886173_effting.json
```

### 7. Visualizar

```powershell
python -m src.web
```

Abre `http://127.0.0.1:8000` no navegador (`--port` muda a porta; `--no-browser` não abre sozinho). A interface tem duas visões, escolhidas na barra lateral:

- **Linha do tempo** — as ocorrências agrupadas por ano, ordenadas pela data do evento quando ela está no texto (ou foi calculada pela data de publicação) e pela data de publicação quando não há — sempre rotulado qual é qual. Filtros por tipo de evento, situação da revisão e periódico; a caixa de busca do topo (atalho `/`) procura em nomes, lugares e no trecho. Cada ocorrência pode ser aprovada ou rejeitada; em "Detalhes" aparecem as pessoas, os lugares e as relações extraídas, que podem ser confirmadas ou rejeitadas. Os aprovados podem ser exportados em CSV ou JSON.
- **Árvore** — as relações de parentesco (filho de, casou com, viúvo de) desenhadas por geração, com casais lado a lado. Linha sólida = relação confirmada; tracejada = hipótese ainda não revisada; vermelha = ciclo ou datas incompatíveis. Clicar numa pessoa abre as evidências de cada ligação, com link para a página original. Arraste para mover; `Ctrl` + roda do mouse ou os botões dão zoom.
- **Pessoas** — cada pessoa com o sobrenome, com as grafias do OCR agrupadas ("Gregorio", "Gresorio", "GregO' rio" e "Jregorio Westrupp" são a mesma pessoa; "Sérgio" e "Sérgio Luiz Westrupp" também, quando só há um "Sérgio ..." completo). Mostra quantas vezes e em que anos aparece, os tratamentos ("Pe.", "Maestro") e cada ocorrência. O agrupamento é uma hipótese: "Não é esta pessoa" separa uma grafia, "Juntar" une duas pessoas, e os ajustes ficam em `data/annotations/people_<conjunto>.json`. A árvore usa o mesmo agrupamento. A lógica está em `src/resolution/people.py`.

Em qualquer ocorrência, "Ler mais" (ou "Trecho maior", na ficha da pessoa) busca no PDF os blocos vizinhos da mesma coluna — cerca de 1.400 caracteres, parando no título da matéria seguinte — em vez das poucas linhas do recorte original.

"Nova busca" (no topo ou na barra lateral) roda o pipeline completo, com barra de progresso, em duas abas: **Família** (todas as pessoas com o sobrenome) e **Pessoa** (nome e sobrenome, com a escolha entre busca inteligente e literal; os demais campos — estado e período — são os mesmos). As decisões de revisão ficam em `data/annotations/review_<conjunto>.json`.

## Estrutura do projeto

```text
src/
  pipeline.py         # modo automático: sobrenome → listagem → PDFs → extração → agrupamento
  collector/          # Etapas 1–3: busca e download das páginas
    browser_bn.py         # busca no memoria.bn dirigindo o Chrome instalado (modo automático)
    editions.py           # edição listada e o CSV listagem_<s>.csv
    pdf_collect.py        # download dos PDFs e busca do sobrenome no texto de cada página
    bn_client.py         # busca por índice completo ou lista de ocorrências; ids das ocorrências
    catalog.py            # catálogo de periódicos de uma região
    search_region.py      # busca em todos os periódicos de uma região de uma vez
    ms_ajax_delta.py      # leitura do formato de resposta parcial do site da BN
  preprocessing/       # Etapa 4: normalização e recorte de contexto
    normalize.py
    segment.py
  extraction/          # Etapa 5: pessoas, datas, eventos, relações
    dates.py
    entities.py
    events.py
    relations.py
    pipeline.py
  resolution/          # Etapa 6: agrupamento de menções da mesma pessoa
    features.py
    clustering.py
    pipeline.py
  graph/               # Grafo de relações familiares
    build.py
    validate.py           # detecta ciclos e incompatibilidades temporais
  web/                 # Interface web (linha do tempo, árvore, pessoas, revisão, nova busca)
    server.py             # API sobre os arquivos de data/processed e data/annotations
    static/               # index.html, styles.css, app.js e tree.js (layout da árvore), sem etapa de build
tests/                 # testes automatizados (pytest)
.github/workflows/     # integração contínua: testes a cada push e pull request
data/
  raw/                 # PDFs baixados (cache) e catálogos de periódicos
  processed/           # Ocorrências, extrações e clusters em JSON/JSONL
  annotations/         # Decisões de revisão (aprovado/rejeitado/confirmado)
```

## Exemplo de registro (saída completa, etapa 5)

```json
{
  "id": "uuid5 determinístico (sobrenome + bib + ano + edição + página)",
  "query": "Effting",
  "bib": "886173",
  "periodical": "Commercio de Joinville (SC) - 1900 a 1913",
  "year": 1909,
  "edition": "235",
  "physical_page": 2,
  "source_url": "https://hemeroteca-pdf.bn.gov.br/886173/per886173_1909_00235.pdf#page=2",
  "publication_date": "1909-11-10",
  "ocr_raw": "texto completo da página, sem alterações",
  "text_normalized": "versão com espaços e hifenização corrigidos",
  "context_excerpt": "trecho recortado ao redor do sobrenome",
  "context_bbox": [344.9, 523.1, 440.6, 691.3],
  "column_mix_suspected": true,
  "person_anchor": {"name_raw": "Antonio Effting", "confidence": 0.85},
  "event_type": "obito",
  "dates": [
    {"raw_text": "hontem", "precision": "dia", "is_relative": true,
     "year": 1909, "month": 11, "day": 9, "resolved_from": "publicacao", "date_value": "1909-11-09"}
  ],
  "relations": []
}
```

Datas relativas ("hontem", "sabbado passado", "dia 5 do corrente") e datas sem ano ("3 de maio") são calculadas a partir de `publication_date` (lida do cabeçalho da primeira página do PDF) e marcadas com `"resolved_from": "publicacao"`; a linha do tempo mostra essas datas como "calculadas a partir da data de publicação". Sem data de publicação, a data relativa fica sem resolver (`precision: "relativa"`). As relações são extraídas só do trecho em volta do sobrenome (`context_excerpt`), não da página inteira, que tem outras notícias.

## Testes

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

Os testes cobrem extração de datas, eventos e relações, a busca do sobrenome e de uma pessoa no texto das páginas, o download/cache de PDFs, a listagem, a retomada do pipeline, o agrupamento de pessoas, o grafo e a API da interface web. Não precisam de rede nem de Chrome.

**Integração contínua:** a cada push e pull request, o GitHub Actions (`.github/workflows/tests.yml`) roda os testes no Linux (Python 3.12 e 3.14) e no Windows (Python 3.14), e confere a sintaxe do JavaScript da interface. Também dá para disparar manualmente na aba *Actions* do repositório.

## Limitações conhecidas

- Não existe amostra anotada manualmente (nenhum critério de precisão/cobertura/F1 foi medido formalmente — as validações feitas até aqui compararam o sistema com casos reais conhecidos, não com um gabarito revisado por humanos).
- O OCR da Biblioteca Nacional perde acentuação em parte do texto (sai como `�`); isso não é corrigido automaticamente.
- Extração de pessoas, datas e relações é baseada em regras e expressões regulares, não em modelo treinado — produz alguns falsos positivos (sinalizados na revisão da interface, nunca aceitos automaticamente).
- O agrupamento de menções (etapa 6) não foi validado contra um gabarito; a interface mostra dois métodos de agrupamento lado a lado para comparação, não uma resposta única.
- Descobrir o `bib` de um periódico novo e gerar a lista/índice de edições ainda depende de uma captura manual no navegador (o catálogo da BN está atrás de uma proteção anti-robô).
- Não há remoção de stopwords, pontuação, stemming ou lematização — o texto é mantido próximo do original para permitir revisão humana do contexto.

## Fora de escopo

- pesquisar todo o acervo da Biblioteca Nacional sem um sobrenome específico;
- construir automaticamente uma árvore genealógica definitiva;
- confirmar parentesco usando apenas proximidade de nome ou cluster;
- integrar FamilySearch, cartórios ou outros serviços externos.

## Fontes

- [Hemeroteca Digital Brasileira](https://bndigital.bn.gov.br/hemeroteca-digital/)
- [Perguntas e respostas da BNDigital](https://bndigital.bn.gov.br/perguntas-e-respostas/)
- [Exemplo: Commercio de Joinville](https://bndigital.bn.gov.br/acervo-digital/commercio/886173)

Ao publicar dados, imagens ou trechos obtidos por esta ferramenta, mantenha a referência à Fundação Biblioteca Nacional e observe as orientações de uso do acervo.
