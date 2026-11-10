"""Busca de sobrenome no memoria.bn.gov.br dirigindo um Chrome real.

Por que um navegador real
-------------------------
Desde 03/10/2025 a Hemeroteca Digital exibe um CAPTCHA de imagem ao abrir
páginas no visualizador (DocReader). Além disso, cookies copiados do
navegador para o `httpx` costumam ser recusados (o cookie de sessão fica
amarrado ao navegador que o gerou). Testado ao vivo em 19/10/2026:

* a busca por Local/Período (`hdb/periodico.aspx` → `docmulti.aspx`),
  a ordenação da grade por "Ocorrências" e o "Listar ocorrências"
  (`ListaPastaOcor.aspx`) **não** pedem CAPTCHA quando chamados de dentro
  do próprio navegador (`fetch` na página, mesma origem, mesmos cookies);
* os PDFs em `hemeroteca-pdf.bn.gov.br` também não pedem CAPTCHA;
* o CAPTCHA só aparece ao abrir uma página no DocReader.

Então a estratégia é: nunca abrir o DocReader. Este módulo abre o Chrome
instalado (perfil próprio em `data/raw/.chrome_cdp_profile`), conecta via
CDP com Playwright e executa as chamadas *dentro da página*. Se em algum
momento aparecer um desafio (CAPTCHA/Cloudflare), o script pausa e pede
para você resolvê-lo na janela do Chrome — ele não tenta resolver sozinho.

Etapas
------
1. `listar()`  → grade de periódicos com ocorrência + edições de cada um
   (o mesmo que clicar em "Listar ocorrências...") → `listagem_<s>.csv`.
2. `pdf_collect.coletar()` → para cada edição, baixa o PDF (httpx; se
   falhar, baixa pelo navegador), localiza as páginas com o sobrenome e
   grava `occurrences_region_<s>.jsonl` no formato de `bn_client.Occurrence`.

O modelo da edição e o CSV de listagem ficam em `editions.py`.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from src.collector.editions import EditionHit

logger = logging.getLogger("collector.browser_bn")

HDB_URL = "https://memoria.bn.gov.br/hdb/periodico.aspx"
CDP_PORT = 9222
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
# desafios conhecidos: CAPTCHA de imagem da BN e páginas da Cloudflare
_CHALLENGE_RE = re.compile(r"Digite o c[oó]digo abaixo|Just a moment|Verify you are human|"
                           r"Verifique se voc[eê] [eé] humano|Validar\s*$", re.I | re.M)


# --------------------------------------------------------------------------- Chrome
def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_chrome(profile_dir: Path, port: int = CDP_PORT) -> None:
    """Abre o Chrome instalado com depuração remota, se ainda não estiver aberto."""
    if _port_open(port):
        return
    exe = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None) or shutil.which("chrome")
    if not exe:
        raise RuntimeError("Chrome não encontrado; instale o Google Chrome ou ajuste CHROME_CANDIDATES")
    # caminho absoluto: com um caminho relativo, o Chrome não reconhece o perfil como
    # separado e, se já houver um Chrome aberto, só abre uma aba nele — sem a porta de
    # depuração (o processo lançado sai na hora com código 0)
    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}",
                             "--no-first-run", "--no-default-browser-check", HDB_URL])
    for _ in range(40):
        if _port_open(port):
            return
        time.sleep(0.5)
    hint = (" O Chrome repassou a abertura para uma janela que já estava aberta; feche as janelas "
            f"do Chrome que usam o perfil {profile_dir} e tente de novo.") if proc.poll() == 0 else ""
    raise RuntimeError("Chrome não abriu a porta de depuração a tempo." + hint)


def wait_if_challenge(page, timeout_s: int = 300) -> None:
    """Se houver CAPTCHA/Cloudflare na tela, espera a pessoa resolver na janela."""
    deadline = time.time() + timeout_s
    warned = False
    while True:
        try:
            text = page.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''")
        except Exception:
            text = ""
        if not _CHALLENGE_RE.search(text or ""):
            return
        if not warned:
            print("\n>>> A BN pediu uma verificação (CAPTCHA). Resolva na janela do Chrome; "
                  "o script continua sozinho depois.\n")
            warned = True
        if time.time() > deadline:
            raise TimeoutError("verificação não resolvida a tempo")
        time.sleep(2)


# --------------------------------------------------------------------------- JS executado na página
# Os controles Telerik/ASP.NET do site fazem postback com `__doPostBack`, que percorre
# `arguments.callee.caller` na pilha de chamadas. O `page.evaluate` do Playwright roda em
# modo estrito, e aí esse acesso estoura ("'caller', 'callee', and 'arguments' properties
# may not be accessed on strict mode functions"). Por isso toda ação que dispara postback
# é injetada como <script> comum e executada num setTimeout: a pilha começa limpa, só com
# código não estrito da própria página.
_JS_INJECT = """(code) => {
  window.__fhsErr = null;
  const s = document.createElement('script');
  s.textContent = 'setTimeout(function(){ try { ' + code + ' } catch (e) { window.__fhsErr = String(e); } }, 0);';
  (document.head || document.documentElement).appendChild(s);
  s.remove();
  return true;
}"""

_JS_BUSY = """() => {
  try { if (window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack()) return true; } catch (e) {}
  const lp = [...document.querySelectorAll('[id^="RadAjaxLoadingPanel"]')].some(e => e.offsetParent !== null);
  return lp;
}"""


def _run_sloppy(target, code: str) -> None:
    """Executa `code` na página/frame fora do modo estrito (ver comentário acima)."""
    target.evaluate(_JS_INJECT, code)
    time.sleep(0.3)
    try:
        err = target.evaluate("window.__fhsErr")
    except Exception:          # a ação navegou para outra página: sem erro a reportar
        return
    if err:
        raise RuntimeError(f"erro no site ao executar {code[:60]!r}: {err}")


def _wait_idle(target, *, timeout_s: float = 120, settle: int = 3) -> None:
    """Espera o site terminar os postbacks assíncronos (painel "Aguarde...")."""
    deadline = time.time() + timeout_s
    quiet = 0
    while time.time() < deadline:
        try:
            busy = target.evaluate(_JS_BUSY)
        except Exception:      # navegação em andamento
            busy = True
        quiet = 0 if busy else quiet + 1
        if quiet >= settle:
            return
        time.sleep(0.5)
    raise TimeoutError("o site da BN não terminou de carregar a tempo")


_JS_GRID_SIG = """() => { const g = document.querySelector('#ListaRadGrid_ctl00 tbody'); return g ? g.innerText : ''; }"""


def _safe_sig(page) -> str | None:
    try:
        return page.evaluate(_JS_GRID_SIG)
    except Exception:
        return None


def _wait_grid(page, before: str | None = None, *, timeout_s: float = 120) -> list[dict]:
    """Espera a grade terminar de atualizar (sort/paginação): conteúdo diferente de `before`,
    coluna Ocorrências em ordem decrescente e sem mudar por 3 leituras seguidas. Sem `before`,
    ou se nada mudar até o fim do prazo, aceita a grade como está."""
    deadline = time.time() + timeout_s
    last, same = None, 0
    while time.time() < deadline:
        # ordenar/paginar às vezes recarrega a página inteira (postback completo) em vez de
        # atualizar só a grade: enquanto recarrega, o evaluate falha — espera e tenta de novo
        try:
            sig = page.evaluate(_JS_GRID_SIG)
            rows = page.evaluate(_JS_GRID)
        except Exception:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                pass
            last, same = None, 0
            time.sleep(1)
            continue
        ocor = [r["ocor"] for r in rows]
        ordered = all(a >= b for a, b in zip(ocor, ocor[1:]))
        same = same + 1 if sig == last else 0
        last = sig
        if rows and ordered and same >= 2 and (before is None or sig != before):
            return rows
        time.sleep(1)
    logger.warning("a grade não mudou depois de ordenar/paginar; usando o que está na tela")
    page.wait_for_selector("#ListaRadGrid_ctl00", timeout=60_000)
    return page.evaluate(_JS_GRID)


def _wait_combo(frame, combo_id: str, timeout_s: float = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        n = frame.evaluate(f"(() => {{ const c = window.$find && $find('{combo_id}'); return c ? c.get_items().get_count() : 0; }})()")
        if n:
            return
        time.sleep(0.5)
    raise TimeoutError(f"lista {combo_id} não carregou")


_JS_GRID = """() => [...document.querySelectorAll('#ListaRadGrid_ctl00 tr.rgRow, #ListaRadGrid_ctl00 tr.rgAltRow')].map(tr => {
  const td = tr.querySelectorAll('td');
  const m = tr.innerHTML.match(/showMenu\\(event,(?:&quot;|")([^&"]+)/);
  return {titulo: td[0].textContent.trim(), paginas: +td[1].textContent.trim(),
          ocor: +td[2].textContent.trim(), bib: m && m[1]};
})"""

_JS_LISTA = """async ({bib, termo}) => {
  const sid = document.getElementById('HiddenID').value;
  const r = await fetch(`/DocReader/ListaPastaOcor.aspx?bib=${bib}&SessaoID=${sid}&pesq=${encodeURIComponent(termo)}`);
  const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
  return [...doc.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))
         .filter(c => c.length >= 2);
}"""

_JS_LISTA_MANY = """async ({bibs, termo, conc}) => {
  const sid = document.getElementById('HiddenID').value;
  const out = {}; let i = 0;
  async function worker() {
    while (i < bibs.length) {
      const bib = bibs[i++];
      try {
        const r = await fetch(`/DocReader/ListaPastaOcor.aspx?bib=${bib}&SessaoID=${sid}&pesq=${encodeURIComponent(termo)}`);
        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
        const rows = [...doc.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))
                     .filter(c => c.length >= 2);
        if (rows.length) out[bib] = rows;
      } catch (e) { out[bib] = 'erro: ' + e; }
    }
  }
  await Promise.all(Array.from({length: conc}, worker));
  return out;
}"""

_PASTA_RES = [
    re.compile(r"ano (\d{4})\\edicao (\w+)"),
    re.compile(r"per\w+?_(\d{4})_(\w+)"),
]


def _parse_pasta(pasta: str) -> tuple[int, str] | None:
    for rx in _PASTA_RES:
        m = rx.search(pasta)
        if m:
            return int(m.group(1)), m.group(2)
    return None


# --------------------------------------------------------------------------- Fase 1

def _open_local_tab(frame) -> bool:
    """O formulário tem 3 abas (Periódico | Período | Local). Tenta abrir a aba "Local" para
    deixar o campo/botão da busca por Local visíveis. Não é obrigatório: a busca em si é
    disparada por script (ver `listar`), que funciona com a aba fechada. Devolve True se o
    campo ficou visível."""
    try:
        if frame.locator("#PesquisaTxt3").is_visible():
            return True
        # o texto "Local" da aba (há outro "Local" escondido, o rótulo do combo)
        handle = frame.evaluate_handle("""() => [...document.querySelectorAll('a, span, li, div, td')]
            .find(e => e.textContent.trim() === 'Local' && e.offsetParent !== null && e.children.length <= 1)""")
        el = handle.as_element()
        if el is not None:
            el.click(timeout=5_000)
            frame.locator("#PesquisaTxt3").wait_for(state="visible", timeout=5_000)
            return True
    except Exception as exc:
        logger.info("aba Local não abriu pelo clique (%s); seguindo por script", str(exc).splitlines()[0])
    return False


def _fechar_resultados_antigos(page) -> None:
    """Fecha abas de resultado (`docmulti.aspx`) deixadas por buscas anteriores. Sem isso,
    `_find_results` pode achar a grade velha em vez da nova — que, já expandida/ordenada,
    não muda mais e faz a leitura da grade esperar até o tempo limite. O Chrome usa um
    perfil só do script, então essas abas são todas dele."""
    for pg in list(page.context.pages):
        if pg is not page and "docmulti.aspx" in pg.url:
            try:
                pg.close()
                logger.info("aba de resultado de uma busca anterior fechada")
            except Exception:
                pass


def _is_bn_page(pg) -> bool:
    """Aba do site da BN. Outras abas do mesmo Chrome (ex.: uma pesquisa no Google aberta
    pela pessoa) podem estar congeladas pelo Chrome em segundo plano — e um `evaluate`
    numa aba congelada nunca retorna, travando o script. Só abas da BN são inspecionadas."""
    try:
        return "bn.gov.br" in (pg.url or "")
    except Exception:
        return False


def _find_results(page, timeout_s: float = 180, debug_dir: Path | None = None):
    """Depois de clicar em Pesquisar, a grade (`docmulti.aspx`) pode abrir na mesma aba,
    dentro do iframe ou numa aba nova (depende do Chrome). Procura em todas as abas e
    frames e devolve onde ela estiver (Page ou Frame).

    Primeiro olha só os endereços (não depende da aba responder); o texto da página, para
    detectar CAPTCHA, só é lido na aba do próprio script."""
    deadline = time.time() + timeout_s
    warned = False
    while time.time() < deadline:
        pages = list(page.context.pages)
        for pg in pages:
            try:
                if "docmulti.aspx" in pg.url:
                    return pg
                for fr in pg.frames:
                    if "docmulti.aspx" in fr.url:
                        return fr
            except Exception:
                continue
        # o CAPTCHA aparece na aba do próprio script; ela está em primeiro plano e responde
        for pg in [page] if _is_bn_page(page) else []:
            try:
                text = pg.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''")
            except Exception:
                continue
            if _CHALLENGE_RE.search(text or "") and not warned:
                print("\n>>> A BN pediu uma verificação (CAPTCHA). Resolva na janela do Chrome; "
                      "o script continua sozinho depois.\n")
                warned = True
        time.sleep(1)
    # não achou: guarda o que estava na tela para diagnóstico (só abas da BN)
    info = []
    for i, pg in enumerate(filter(_is_bn_page, page.context.pages)):
        info.append(f"aba {i}: {pg.url} | frames: {[f.url for f in pg.frames]}")
        if debug_dir is not None:
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(debug_dir / f"debug_busca_aba{i}.png"), full_page=True)
                (debug_dir / f"debug_busca_aba{i}.html").write_text(pg.content(), encoding="utf-8")
            except Exception:
                pass
    raise TimeoutError("a página de resultados (docmulti.aspx) não abriu depois de Pesquisar.\n  "
                       + "\n  ".join(info)
                       + (f"\n  Capturas salvas em {debug_dir}" if debug_dir else ""))

_JS_GRID_STATE = """() => {
  const txt = (document.body ? document.body.innerText : '') + '\\n' +
              [...document.querySelectorAll('input[type=text], input:not([type])')].map(i => i.value).join('\\n');
  const num = s => s ? +s.replace(/\\./g, '') : null;
  const pager = txt.match(/de\\s+([\\d.]+)\\s*$/m) || txt.match(/acervo\\s+\\d+\\s+at[ée]\\s+\\d+\\s+de\\s+([\\d.]+)/i);
  const oc = txt.match(/Ocorr\\S{1,3}ncias\\s*([\\d.]+)/);
  const ac = txt.match(/Acervos\\s*([\\d.]+)/);
  return {total_ocor: oc ? num(oc[1]) : null, total_acervos: ac ? num(ac[1]) : (pager ? num(pager[1]) : null)};
}"""


def _grid_state(page) -> dict:
    try:
        return page.evaluate(_JS_GRID_STATE)
    except Exception:
        return {"total_ocor": None, "total_acervos": None}


def _rows_safe(page) -> list[dict] | None:
    try:
        return page.evaluate(_JS_GRID)
    except Exception:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        return None


def _collect_periodicos(page) -> list[dict]:
    """Lê os periódicos com ocorrência da grade de resultados.

    Estratégia A: aumenta o tamanho da página da grade para caber todos os acervos (uma
    única recarga) e filtra Ocorrências > 0. Estratégia B (se A não funcionar): ordena por
    Ocorrências decrescente e pagina. O total de ocorrências mostrado no topo da página
    (ex.: "Ocorrências 59") serve de conferência."""
    st = _grid_state(page)
    logger.info("grade: %s acervo(s), %s ocorrência(s) segundo o topo da página",
                st["total_acervos"], st["total_ocor"])
    if st["total_ocor"] == 0:
        return []
    want = st["total_acervos"] or 2000

    # --- A: tudo numa página só
    rows: list[dict] = []
    before = _safe_sig(page)
    try:
        _run_sloppy(page, f"$find('ListaRadGrid_ctl00').set_pageSize({int(want) + 10});")
        deadline = time.time() + 60
        while time.time() < deadline:
            r = _rows_safe(page)
            if r is not None and len(r) >= min(want, 51) and _safe_sig(page) != before:
                time.sleep(2)
                r2 = _rows_safe(page)
                if r2 is not None and len(r2) == len(r):
                    rows = r2
                    break
            time.sleep(1)
    except Exception as exc:
        logger.info("não consegui aumentar a página da grade (%s)", str(exc).splitlines()[0])
    hits = [r for r in rows if r["ocor"] > 0 and r["bib"]]
    total = sum(h["ocor"] for h in hits)
    if hits and (st["total_ocor"] is None or total >= st["total_ocor"]):
        logger.info("grade completa lida de uma vez: %d linhas", len(rows))
        return hits
    if rows:
        logger.info("grade em uma página só: %d linhas, %d ocorrências (esperado %s) — tentando ordenar",
                    len(rows), total, st["total_ocor"])

    # --- B: ordena por Ocorrências e pagina
    before = _safe_sig(page)
    _run_sloppy(page, "$find('ListaRadGrid_ctl00').sort('OcorNro DESC');")
    rows = _wait_grid(page, before)
    logger.info("grade depois de ordenar: %d linha(s); primeiras: %s", len(rows),
                [(r["titulo"][:25], r["ocor"], r["bib"]) for r in rows[:3]])
    if rows and not any(r["bib"] for r in rows):
        try:
            logger.warning("não achei o código (bib) nas linhas da grade; HTML da 1ª linha: %s",
                           page.evaluate("document.querySelector('#ListaRadGrid_ctl00 tr.rgRow').outerHTML")[:600])
        except Exception:
            pass
    periodicos: list[dict] = []
    seen: set[str] = set()
    for _ in range(100):
        wait_if_challenge(page)
        for r in rows:
            if r["ocor"] > 0 and r["bib"] and r["bib"] not in seen:
                seen.add(r["bib"])
                periodicos.append(r)
        if not rows or rows[-1]["ocor"] == 0:
            break
        before = _safe_sig(page)
        _run_sloppy(page, "$find('ListaRadGrid_ctl00').page('Next');")
        rows = _wait_grid(page, before)
    total = sum(p["ocor"] for p in periodicos)
    if st["total_ocor"] and total < st["total_ocor"]:
        logger.warning("a grade somou %d ocorrências, mas o topo da página diz %d — a lista pode estar incompleta",
                       total, st["total_ocor"])
    return periodicos


def _listar_ocorrencias(page, bibs: list[str], termo: str, *, nomes: dict[str, str],
                        progress, delay: float, lote: int = 20, conc: int = 4,
                        verbose: bool = False) -> dict[str, list]:
    """"Listar ocorrências" de vários periódicos, `conc` requisições simultâneas dentro da
    página (`_JS_LISTA_MANY`), em lotes de `lote` para a barra de progresso andar. Se um lote
    inteiro falhar (ex.: a página recarregou), refaz esse lote um periódico por vez."""
    listas: dict[str, list] = {}
    for i in range(0, len(bibs), lote):
        chunk = bibs[i:i + lote]
        try:
            res = page.evaluate(_JS_LISTA_MANY, {"bibs": chunk, "termo": termo, "conc": conc})
        except Exception as exc:
            logger.warning("consulta em lote falhou (%s); refazendo um a um", str(exc).splitlines()[0])
            res = {}
            for bib in chunk:
                try:
                    res[bib] = page.evaluate(_JS_LISTA, {"bib": bib, "termo": termo})
                except Exception as exc1:
                    res[bib] = f"erro: {str(exc1).splitlines()[0]}"
                time.sleep(delay)
        for bib, rows in res.items():
            if isinstance(rows, list):
                listas[bib] = rows
            else:
                logger.warning("Listar ocorrências falhou em %s (%s)", nomes.get(bib, bib), rows)
        feitos = min(i + lote, len(bibs))
        if verbose:
            logger.info("   %d/%d periódicos consultados — %d com ocorrência",
                        feitos, len(bibs), sum(1 for v in listas.values() if v))
        progress(feitos, len(bibs))
    return listas


def _decada_do_intervalo(ano_inicio: int | None, ano_fim: int | None) -> int | None:
    """Década (ex. 1940) quando o intervalo cabe inteiro numa só; senão None."""
    if ano_inicio is None or ano_fim is None:
        return None
    d = ano_inicio - ano_inicio % 10
    return d if ano_fim <= d + 9 else None


def _selecionar_periodo(frame, ano_inicio: int | None, ano_fim: int | None) -> None:
    """Escolhe o item do combo de Período. O combo só aceita uma década por vez
    (itens tipo "1940 - 1949", carregados conforme o Local escolhido): se o
    intervalo pedido cabe numa década, escolhe ela; senão fica em "Todos" e o
    corte por ano é feito depois, sobre a listagem (ver `editions.filtrar_periodo`)."""
    itens = [t.strip() for t in frame.evaluate(
        "$find('PeriodoCmb3').get_items().toArray().map(i => i.get_text())")]
    logger.info("períodos disponíveis no site: %s", ", ".join(itens))
    decada = _decada_do_intervalo(ano_inicio, ano_fim)
    idx = 0                                                      # "Todos"
    if decada is not None:
        alvo = re.compile(rf"^\s*{decada}\s*[-–]\s*{decada + 9}\s*$")
        achado = next((i for i, t in enumerate(itens) if alvo.match(t)), None)
        if achado is None:
            logger.warning("a década %d-%d não aparece no combo de Período do site; "
                           "buscando em todos e filtrando por ano depois", decada, decada + 9)
        else:
            idx = achado
    _run_sloppy(frame, f"$find('PeriodoCmb3').get_items().getItem({idx}).select();")


LOCAIS_FILE = "memoria_locais.json"


def salvar_locais(frame, path: Path) -> None:
    """Guarda os itens do combo "Local" do formulário (UFs e outros locais que o memoria.bn
    oferece), para a interface montar a lista de estados sem precisar abrir o site. Nunca
    interrompe a busca: se não der para ler, só registra no log."""
    try:
        itens = [t.strip() for t in frame.evaluate(
            "$find('UFCmb3').get_items().toArray().map(i => i.get_text())") if t and t.strip()]
        itens = [t for t in itens if not t.lower().startswith("todos")]
        if itens:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"locais": itens, "capturado_em": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.info("não consegui guardar a lista de locais (%s)", str(exc).splitlines()[0])


def listar(page, surname: str, uf: str = "SC", *, ano_inicio: int | None = None,
           ano_fim: int | None = None, delay: float = 0.8, debug_dir: Path | None = None,
           progress=None, locais_path: Path | None = None) -> list[EditionHit]:
    """Faz a busca por Local (e Período, quando o intervalo cabe numa década),
    ordena por Ocorrências e chama "Listar ocorrências" para cada periódico
    com resultado. Nada é baixado nesta fase.

    `progress(feitos, total)`, se informado, é chamado a cada lote de
    periódicos consultados no "Listar ocorrências" (4 consultas simultâneas)."""
    progress = progress or (lambda feitos, total: None)
    _fechar_resultados_antigos(page)
    logger.info("browser_bn v8: abrindo %s", HDB_URL)
    page.goto(HDB_URL)
    wait_if_challenge(page)
    page.wait_for_selector("iframe")
    frame = None
    for _ in range(120):
        frame = next((f for f in page.frames if "DefaultNew.aspx" in f.url), None)
        if frame:
            break
        time.sleep(0.5)
    if frame is None:
        raise TimeoutError("o formulário de busca do memoria.bn não carregou")
    _wait_combo(frame, "UFCmb3")
    if locais_path is not None:
        salvar_locais(frame, locais_path)
    _open_local_tab(frame)
    if not frame.evaluate(f"!!$find('UFCmb3').findItemByText({json.dumps(uf)})"):
        raise ValueError(f"Local {uf!r} não existe no memoria.bn")
    _run_sloppy(frame, f"$find('UFCmb3').findItemByText({json.dumps(uf)}).select();")
    _wait_combo(frame, "PeriodoCmb3")
    _wait_idle(frame)
    _selecionar_periodo(frame, ano_inicio, ano_fim)
    _wait_combo(frame, "PeriodicoCmb3")
    _wait_idle(frame)
    _run_sloppy(frame, "$find('PeriodicoCmb3').get_items().getItem(0).select();")   # Todos
    _wait_idle(frame)
    time.sleep(2)
    catalogo = {v: t.strip() for v, t in frame.evaluate(
        "$find('PeriodicoCmb3').get_items().toArray().map(i => [i.get_value(), i.get_text()])") if v}
    logger.info("%d periódicos de %s no formulário", len(catalogo), uf)
    sel = frame.evaluate("[$find('UFCmb3').get_text(), $find('PeriodoCmb3').get_text(), $find('PeriodicoCmb3').get_text()]")
    logger.info("formulário: Local=%s | Período=%s | Periódico=%s", *[x.strip() for x in sel])
    # Preenche o termo e clica em Pesquisar por script: funciona mesmo com a aba "Local"
    # fechada (o campo fica invisível e o Playwright se recusa a digitar nele).
    _open_local_tab(frame)
    _run_sloppy(frame, "var t = document.getElementById('PesquisaTxt3'); t.value = " + json.dumps(surname)
                + "; document.getElementById('PesquisarBtn3').click();")
    try:
        page = _find_results(page, timeout_s=45, debug_dir=None)
    except TimeoutError:
        # plano B: clique "de verdade" (caso o site abra o resultado numa aba nova e o Chrome
        # tenha bloqueado o pop-up do clique por script)
        logger.info("resultado não abriu com o clique por script; tentando clique real")
        if _open_local_tab(frame):
            frame.fill("#PesquisaTxt3", surname)
            frame.click("#PesquisarBtn3")
        page = _find_results(page, debug_dir=debug_dir)
    # daqui em diante `page` é onde a grade estiver (aba ou frame)
    wait_if_challenge(page)
    page.wait_for_selector("#ListaRadGrid_ctl00", timeout=120_000)
    _wait_idle(page)
    time.sleep(3)              # a BN termina de calcular as contagens logo depois de abrir a grade

    try:
        periodicos = _collect_periodicos(page)
    except Exception as exc:
        logger.warning("falha ao ler a grade de resultados (%s)", str(exc).splitlines()[0])
        periodicos = []
    logger.info("grade: %d periódico(s) com ocorrência (%d ocorrências)",
                len(periodicos), sum(p["ocor"] for p in periodicos))
    for p in periodicos[:5]:
        logger.info("   %s — %d", p["titulo"], p["ocor"])

    listas = _listar_ocorrencias(page, [p["bib"] for p in periodicos], surname,
                                 nomes={p["bib"]: p["titulo"] for p in periodicos},
                                 progress=progress, delay=delay)

    if not any(listas.values()):
        # Plano B, sem depender da grade: pergunta o "Listar ocorrências" de cada periódico do
        # estado (a lista de periódicos vem do próprio formulário de busca).
        logger.warning("a grade não trouxe periódicos; consultando os %d periódicos de %s um a um "
                       "(pode levar alguns minutos)", len(catalogo), uf)
        listas = _listar_ocorrencias(page, list(catalogo), surname, nomes=catalogo,
                                     progress=progress, delay=delay, lote=60, verbose=True)
        periodicos = [{"bib": b, "titulo": catalogo.get(b, b), "ocor": len(v)} for b, v in listas.items() if v]

    titulos = {p["bib"]: p["titulo"] for p in periodicos}
    editions: list[EditionHit] = []
    seen: set[tuple] = set()
    for bib, linhas in listas.items():
        for cols in linhas or []:
            parsed = _parse_pasta(cols[0])
            if not parsed:
                continue
            ano, ed = parsed
            key = (bib, ano, ed)
            if key in seen:            # a BN às vezes repete a mesma pasta
                continue
            seen.add(key)
            n = int(cols[-1]) if cols[-1].isdigit() else 1
            editions.append(EditionHit(bib, titulos.get(bib) or catalogo.get(bib, bib), ano, ed, n, pasta=cols[0]))
    if periodicos and not editions:
        amostra = next((v for v in listas.values() if v), None)
        logger.warning("havia periódicos com ocorrência, mas nenhuma edição foi reconhecida. "
                       "Amostra do 'Listar ocorrências': %r", (amostra or [])[:3])
    logger.info("%d edição(ões) listadas", len(editions))
    return editions


class BrowserSession:
    """`with BrowserSession(data_dir) as (context, page): ...`"""

    def __init__(self, data_dir: Path, port: int = CDP_PORT):
        self.profile = data_dir / "raw" / ".chrome_cdp_profile"
        self.port = port

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        ensure_chrome(self.profile, self.port)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = self.context.new_page()
        return self.context, self.page

    def __exit__(self, *exc):
        try:
            self.page.close()
        finally:
            self._pw.stop()   # desconecta; o Chrome continua aberto para a próxima busca
