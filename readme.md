# Family History Search

Ferramenta de linha de comando para buscar um sobrenome no acervo de jornais e revistas da Hemeroteca Digital Brasileira (Biblioteca Nacional), transformar as ocorrências encontradas em eventos estruturados (pessoas, datas, lugares, relações) e apresentar tudo numa linha do tempo e num grafo de parentesco navegáveis.

O sobrenome é um parâmetro de entrada, não algo fixo no código — a ferramenta funciona para qualquer sobrenome. Os exemplos deste README usam `Effting` porque foi o caso usado para construir e validar o projeto, mas o mesmo fluxo serve para pesquisar qualquer outro nome de família.

> O sistema não afirma parentescos. Cada pessoa, evento e relação extraída carrega o trecho de evidência, o link para a página original e um nível de confiança. Agrupamentos de menções que podem ser a mesma pessoa são apresentados como hipóteses revisáveis, nunca como fatos confirmados automaticamente.

## O que o projeto faz

Dado um sobrenome, o pipeline:

1. busca ocorrências em periódicos da Hemeroteca Digital e baixa as páginas correspondentes;
2. extrai o texto de cada página (a Biblioteca Nacional já disponibiliza OCR embutido no PDF — não é necessário rodar OCR local) e recorta o trecho relevante ao redor do sobrenome, com sua posição na página;
3. reconhece pessoas, datas (completas, parciais ou relativas), lugares, tipo de evento (nascimento, casamento, óbito, outro) e relações explícitas (`filho_de`, `casou_com`, `viuvo_de`, `residente_em`, `faleceu_em`) em cada trecho;
4. agrupa, com uma pontuação explicável, quais menções provavelmente são a mesma pessoa;
5. mostra tudo numa aplicação Streamlit: uma linha do tempo filtrável e revisável, e um grafo interativo das relações extraídas.

## Instalação

```powershell
pip install -r requirements.txt
```

Isso instala as dependências e o modelo de reconhecimento de entidades em português do spaCy. Se preferir instalar o modelo à parte: `python -m spacy download pt_core_news_sm`.

## Como usar

### Modo automático (recomendado)

Se a região já tem um catálogo capturado (ex.: `data/periodicos_sc.json`, com os periódicos de Santa Catarina), a busca inteira — descobrir quais periódicos/edições têm o sobrenome, baixar, extrair e agrupar — roda com um único comando, sem nenhuma etapa manual:

```powershell
python -m src.pipeline --surname Effting --catalog data/periodicos_sc.json
```

Na primeira execução (ou quando a sessão expirar), o comando abre o **Chrome instalado na máquina** (perfil próprio em `data/raw/.chrome_cdp_profile`, separado do seu perfil pessoal) para passar pelo desafio da Cloudflare do `memoria.bn.gov.br` e captura cookie/`SessaoID` automaticamente conectando nele via CDP (`src/collector/session_auth.py`) — resolva o desafio manualmente na janela se ele aparecer. A sessão fica salva em `data/raw/session.json` e é reaproveitada nas próximas buscas. Testamos ao vivo: um browser *lançado pelo Playwright* (headless ou headed) é bloqueado pela Cloudflare em ambos os modos — por isso a captura conecta num Chrome real aberto normalmente, não num Chromium controlado do zero. Ver `ESTRATEGIA_BUSCA_SOBRENOME.md` para os detalhes.

O mesmo fluxo está disponível na aba "Buscar novo sobrenome" da interface Streamlit (passo 7 abaixo): digite o sobrenome, escolha o catálogo e clique em "Buscar".

Isso gera `data/processed/occurrences_region_<sobrenome>_extracted.jsonl` e `person_clusters_region_<sobrenome>.json`, prontos pra visualização (passo 7). As seções abaixo documentam as etapas manuais equivalentes, úteis para depurar uma etapa isoladamente ou pra periódicos fora de um catálogo já capturado.

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
python -m streamlit run src/visualization/app.py
```

Abre uma aplicação com duas abas: **linha do tempo** (ordenada por data do evento quando extraída, ou data de publicação quando não há data no texto — sempre rotulado qual é qual) e **grafo de relações** (nós são pessoas, arestas são relações, tracejado laranja para hipótese e verde sólido para confirmada). Dá para aprovar, rejeitar ou confirmar cada ocorrência e cada relação direto na interface; as decisões ficam salvas em `data/annotations/` e já entram no grafo na próxima atualização.

## Estrutura do projeto

```text
src/
  collector/          # Etapas 1–3: busca e download das páginas
    bn_client.py         # busca por índice completo ou lista de ocorrências
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
    render.py
  visualization/       # Aplicação Streamlit (timeline + grafo)
    app.py
data/
  raw/                 # PDFs baixados (cache) e catálogos de periódicos
  processed/           # Ocorrências, extrações e clusters em JSON/JSONL
  annotations/         # Decisões de revisão (aprovado/rejeitado/confirmado)
```

## Exemplo de registro (saída completa, etapa 5)

```json
{
  "id": "uuid",
  "query": "Effting",
  "bib": "886173",
  "periodical": "Commercio de Joinville (SC) - 1900 a 1913",
  "year": 1909,
  "edition": "235",
  "physical_page": 2,
  "source_url": "https://hemeroteca-pdf.bn.gov.br/886173/per886173_1909_00235.pdf#page=2",
  "ocr_raw": "texto completo da página, sem alterações",
  "text_normalized": "versão com espaços e hifenização corrigidos",
  "context_excerpt": "trecho recortado ao redor do sobrenome",
  "context_bbox": [344.9, 523.1, 440.6, 691.3],
  "column_mix_suspected": true,
  "person_anchor": {"name_raw": "Antonio Effting", "confidence": 0.85},
  "event_type": "outro",
  "dates": [],
  "relations": []
}
```

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
