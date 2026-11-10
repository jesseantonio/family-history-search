# Estratégia: busca de sobrenome ponta a ponta

> Documento histórico do planejamento. A interface Streamlit citada aqui foi substituída
> pela interface web em `src/web` (`python -m src.web`); o fluxo descrito virou `src/pipeline.py`.

Objetivo: o usuário digita um sobrenome, o sistema pesquisa na Hemeroteca
Digital e devolve direto a linha do tempo e as ocorrências tratadas, do
jeito que a interface Streamlit já mostra hoje — sem passar manualmente
pelas seis etapas do pipeline (`collector` → `preprocessing` → `extraction`
→ `resolution` → visualização).

## Diagnóstico: onde está o bloqueio real

O pipeline atual (ver `readme.md`) trata toda a busca como dependente de
captura manual no navegador. Olhando o código com mais cuidado, isso só é
verdade pra parte dele:

- **`bndigital.bn.gov.br`** (índice completo de edições de um periódico,
  usado em `bn_client.PeriodicalIndex`) — **está atrás de Cloudflare**.
  Não automatizável sem automação de navegador.
- **`memoria.bn.gov.br/DocReader/ListaPastaOcor.aspx`** (edições onde um
  termo aparece dentro de um periódico, usado em `search_region.py`) —
  **não tem Cloudflare** (confirmado no docstring de `catalog.py`: "Essa
  página funciona sem Cloudflare"). Só exige um cookie de sessão e um
  `SessaoID` válidos.
- **Catálogo de periódicos de uma região** (`catalog.py`, já resolvido —
  `data/periodicos_sc.json` tem os 1051 periódicos de SC com seus `bib`) —
  **não depende do sobrenome pesquisado**. É baixado uma vez e serve pra
  qualquer busca futura.

Ou seja: o fluxo hoje (`search_region.py --ranked-grid ...`) usa uma
captura manual do grid `docmulti.aspx` ordenado por "Ocorrências" **só
para descobrir quais periódicos têm hit** pro termo buscado — isso é uma
otimização, não uma necessidade. Como já temos o catálogo completo, dá pra
pular esse grid: basta chamar `ListaPastaOcor.aspx` para **todos os bibs do
catálogo** com o novo sobrenome como `pesq`.

Isso muda o gargalo de "uma captura manual por sobrenome" para "uma
captura manual por sessão" (cookie + `SessaoID`, reutilizável enquanto não
expirar, pra qualquer sobrenome digitado depois).

## O que fica automático vs. o que continua manual

| Etapa | Hoje | Proposta |
|---|---|---|
| Descobrir bibs de uma região | manual (1x por região) | **sem mudança** — já resolvido em `data/periodicos_sc.json` |
| Descobrir quais periódicos têm o sobrenome | manual (grid por sobrenome) | **automático** — varre o catálogo inteiro via `ListaPastaOcor.aspx` |
| Baixar edições e extrair ocorrências | automático | sem mudança |
| Pré-processamento, extração, resolução | automático (comandos separados) | **encadeado automaticamente** |
| Exibir timeline + grafo | automático (Streamlit) | sem mudança — mesma interface de hoje |
| Cookie/SessaoID de sessão | capturado por sobrenome | **capturado só quando expira**, não por sobrenome |
| Índice completo de periódico novo (fora do catálogo já capturado) | manual, bloqueado por Cloudflare | continua manual — fora do escopo |

## Arquitetura proposta

### 1. Busca no catálogo inteiro (`src/collector/search_region.py`)

Nova função, ao lado de `search_region()`, que não depende do grid
ranqueado:

```python
def search_catalog(
    catalog: list[CatalogEntry],   # todo o catálogo da região, não só quem tem hit
    surname: str,
    sessao_id: str,
    cookie: str,
    cache_dir: Path,
    *,
    delay: float = 1.0,
) -> Iterator[Occurrence]:
    ...  # mesmo corpo de search_region(), trocando `periodicals` (PeriodicalHits)
    ...  # por `catalog` (CatalogEntry) — sem o filtro de "ocorrencias_bn > 0"
```

Mais lento (consulta ~1000 periódicos em vez dos poucos que já sabidamente
têm hit), mas elimina a captura manual por sobrenome.

### 2. Orquestrador (`src/pipeline.py`, novo)

Um comando único que encadeia as etapas hoje separadas:

```powershell
python -m src.pipeline --surname Effting --catalog data/periodicos_sc.json --sessao-id ... --cookie "..."
```

Internamente chama, em sequência, o que já existe:

1. `search_catalog(...)` → grava `data/processed/occurrences_region_<sobrenome>.jsonl`
2. `preprocessing.segment` → `..._enriched.jsonl`
3. `extraction.pipeline` → `..._extracted.jsonl`
4. `resolution.pipeline` → `person_clusters_region_<sobrenome>.json`

Sem inventar formato novo — os nomes de arquivo seguem o padrão que
`visualization/app.py` já escaneia (`*_extracted.jsonl`), então o restante
da interface não muda.

### 3. Formulário no Streamlit (`src/visualization/app.py`)

Adicionar na sidebar, acima dos filtros atuais:

- campo de texto "Sobrenome"
- cookie/`SessaoID` pedidos **uma vez** e salvos em `data/raw/session.json`
  (reaproveitados nas buscas seguintes até expirarem)
- botão "Buscar" que roda o orquestrador (com `st.status`/barra de
  progresso) e, ao terminar, seleciona automaticamente o novo dataset no
  `selectbox` que já existe — a aba de timeline e a de grafo continuam
  exatamente como estão hoje, sem alteração.
- se a sessão tiver expirado (erro de autenticação na primeira chamada),
  mostrar uma mensagem clara pedindo pra recapturar cookie/`SessaoID`, em
  vez de falhar silenciosamente.

## Fora de escopo (permanece manual)

- Descobrir o `bib`/índice completo de um periódico que ainda não está em
  nenhum catálogo de região já capturado (bloqueado por Cloudflare em
  `bndigital.bn.gov.br`).
- Capturar o catálogo de uma região nova pela primeira vez (`catalog.py`) —
  isso é por região, não por sobrenome, e continua raro.

## Próximo passo

Implementar, nessa ordem: `search_catalog()` → `src/pipeline.py` →
formulário no Streamlit. Cada peça é testável isoladamente antes de plugar
na seguinte.
