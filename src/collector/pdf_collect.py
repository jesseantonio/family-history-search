"""Fase 2 da busca: baixa o PDF de cada edição listada e acha as páginas com o sobrenome.

Os PDFs ficam em `hemeroteca-pdf.bn.gov.br`, que não pede CAPTCHA. O download
é feito com `httpx`; se falhar, e houver um Chrome conectado (ver
`browser_bn.BrowserSession`), tenta de novo pelo navegador. Nada aqui
depende de Playwright diretamente: o navegador entra só como uma função
opcional que devolve uma aba.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pymupdf
from rapidfuzz.distance import Levenshtein

from src.collector.bn_client import PDF_HOST, Occurrence, is_pdf_bytes, make_occurrence_id
from src.collector.editions import EditionHit
from src.extraction.dates import extract_dates

logger = logging.getLogger("collector.pdf_collect")

# sobrenomes com menos letras que isso só batem de forma exata: com 1 letra de tolerância,
# "Silva" passaria a aceitar "Salva", "Selva"...
MIN_LEN_FUZZY = 6
PUBLICATION_HEADER_CHARS = 1500
# downloads simultâneos do espelho de PDFs: poucos, por educação com o servidor da BN
DEFAULT_WORKERS = 4

_JS_PDF_B64 = """async (url) => {
  const r = await fetch(url);
  if (!r.ok) return {status: r.status};
  const b = new Uint8Array(await r.arrayBuffer());
  let s = ''; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return {status: 200, b64: btoa(s)};
}"""


def norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def page_matches(text: str, needle: str) -> bool:
    """`needle` já normalizado com `norm`. Aceita hifenização de fim de linha ("Saut-\\nner")
    e, para sobrenomes de 6+ letras, 1 letra trocada/faltando/sobrando por erro de OCR
    ("Saurner", "Sautncr"), desde que a primeira letra bata."""
    t = norm(text)
    if needle in t:
        return True
    t2 = re.sub(r"-\s*\n\s*", "", t)
    if needle in t2:
        return True
    if len(needle) < MIN_LEN_FUZZY or not needle.isalpha():
        return False
    for tok in re.findall(r"[a-z]{%d,%d}" % (len(needle) - 1, len(needle) + 1), t2):
        if tok[0] == needle[0] and Levenshtein.distance(tok, needle) <= 1:
            return True
    return False


def publication_date(text: str, ano_pasta: int) -> str | None:
    """Data de publicação impressa no cabeçalho da edição (ex.: "Curityba, Domingo, 3 de
    Janeiro de 1926"). Só aceita datas completas com o ano da pasta da edição, para não
    pegar uma data citada no corpo do texto. None se o OCR não permitir identificar."""
    for d in extract_dates(text[:PUBLICATION_HEADER_CHARS]):
        if d.precision == "dia" and not d.is_relative and d.year == ano_pasta:
            try:
                return date(d.year, d.month, d.day).isoformat()
            except ValueError:
                continue
    return None


def _cached_ok(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return is_pdf_bytes(f.read(1024))
    except OSError:
        return False


def download(ed: EditionHit, cache_dir: Path, client: httpx.Client,
             browser_page: Callable[[], object] | None = None) -> tuple[Path | None, bool]:
    """Devolve (caminho, baixou_agora). Caminho None = 404 no servidor de PDFs.
    Um arquivo em cache que não é PDF (resto de uma página de verificação) é descartado
    e baixado de novo."""
    res = _download_httpx(ed, cache_dir, client)
    return res if res is not None else _download_browser(ed, cache_dir, browser_page)


def _save(dest: Path, data: bytes) -> None:
    tmp = dest.with_suffix(".pdf.part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def _download_httpx(ed: EditionHit, cache_dir: Path, client: httpx.Client) -> tuple[Path | None, bool] | None:
    """Cache ou httpx — a parte do `download` que pode rodar em outra thread (não toca no
    navegador). Mesmo retorno de `download`, ou None se for preciso tentar pelo navegador."""
    dest = cache_dir / ed.bib / ed.pdf_name
    if dest.exists():
        if _cached_ok(dest):
            return dest, False
        logger.warning("%s em cache não é um PDF válido; baixando de novo", dest.name)
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = client.get(ed.pdf_url)
        if r.status_code == 404:
            return None, False
        r.raise_for_status()
        if is_pdf_bytes(r.content):
            _save(dest, r.content)
            return dest, True
        logger.info("%s: o servidor respondeu algo que não é PDF; tentando pelo navegador", ed.pdf_name)
    except httpx.HTTPError as exc:
        logger.info("httpx falhou em %s (%s); tentando pelo navegador", ed.pdf_name, exc)
    return None


def _download_browser(ed: EditionHit, cache_dir: Path,
                      browser_page: Callable[[], object] | None) -> tuple[Path | None, bool]:
    """Plano B pelo Chrome. Só na thread principal: a API síncrona do Playwright não
    pode ser usada de outra thread."""
    bp = browser_page() if browser_page is not None else None
    if bp is not None:
        res = bp.evaluate(_JS_PDF_B64, f"/{ed.bib}/{ed.pdf_name}")
        if res.get("status") == 404:
            return None, False
        if res.get("status") == 200:
            candidate = base64.b64decode(res["b64"])
            if is_pdf_bytes(candidate):
                dest = cache_dir / ed.bib / ed.pdf_name
                _save(dest, candidate)
                return dest, True
    raise RuntimeError(f"não consegui baixar um PDF válido de {ed.pdf_url}")


def coletar(editions: list[EditionHit], surname: str, cache_dir: Path, *, browser_context=None,
            delay: float = 1.0, workers: int = DEFAULT_WORKERS,
            progress: Callable[[int, int], None] | None = None,
            ja_feitas: set[tuple] | None = None,
            ao_concluir: Callable[[EditionHit], None] | None = None,
            person: dict | None = None) -> Iterator[Occurrence]:
    """Baixa cada PDF listado e produz uma Occurrence por página com o sobrenome.

    `person`, numa busca de uma pessoa, é {"query": "Carlos Barletta", "mode": "inteligente",
    "match": função(texto da página) -> nome como lido ou None}: só as páginas em que a
    pessoa aparece viram ocorrência.

    - até `workers` PDFs são baixados ao mesmo tempo (threads, só httpx); a leitura do
      PDF e o plano B pelo navegador ficam na thread principal, e as edições são
      processadas na ordem da lista;
    - `progress(feitas, total)` é chamado conforme as edições são processadas;
    - edições cuja `key` está em `ja_feitas` são puladas (retomada de uma execução
      interrompida);
    - `ao_concluir(edição)` é chamado depois de cada edição processada, já com o
      `status` final (para gravar o ponto de retomada);
    - a pausa `delay` vale por thread e só acontece depois de um download de verdade,
      não para PDFs que já estavam em cache."""
    progress = progress or (lambda feitas, total: None)
    ja_feitas = ja_feitas or set()
    needle = norm(surname).strip()
    pdf_page = None

    def browser_page():
        # aba no domínio dos PDFs, criada só se o httpx falhar (o fetch precisa ser da mesma origem)
        nonlocal pdf_page
        if pdf_page is None and browser_context is not None:
            pdf_page = browser_context.new_page()
            try:
                pdf_page.goto(f"{PDF_HOST}/{editions[0].bib}/", wait_until="commit", timeout=60_000)
            except Exception as exc:
                logger.warning("não abri %s no Chrome (%s)", PDF_HOST, str(exc).splitlines()[0])
        return pdf_page

    navegador = browser_page if browser_context is not None else None
    client = httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 family-history-search"})
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="pdf")

    def baixar(ed: EditionHit):
        res = _download_httpx(ed, cache_dir, client)
        if res is not None and res[1] and delay:
            time.sleep(delay)
        return res

    try:
        # tudo enfileirado de uma vez: só `workers` rodam por vez, e o resultado de cada
        # um é só o caminho do arquivo em disco (nada de PDF inteiro na memória)
        futuros = {ed.key: pool.submit(baixar, ed) for ed in editions if ed.key not in ja_feitas}
        for feitas, ed in enumerate(editions):
            progress(feitas, len(editions))
            if ed.key in ja_feitas:
                continue
            yield from _coletar_edicao(ed, surname, needle, cache_dir, futuros[ed.key], navegador, delay, person)
            if ao_concluir is not None:
                ao_concluir(ed)
        progress(len(editions), len(editions))
    finally:
        # interrompido (erro, Ctrl+C) ou terminado: descarta o que ainda nem começou e
        # espera só os downloads em andamento antes de fechar o cliente
        pool.shutdown(wait=True, cancel_futures=True)
        client.close()
        if pdf_page is not None:
            pdf_page.close()


def _coletar_edicao(ed: EditionHit, surname: str, needle: str, cache_dir: Path, futuro: Future,
                    browser_page, delay: float, person: dict | None = None) -> Iterator[Occurrence]:
    ed.paginas_pdf = []
    try:
        res = futuro.result()
        if res is None:                       # httpx falhou: tenta pelo navegador, aqui
            res = _download_browser(ed, cache_dir, browser_page)
            if res[1] and delay:
                time.sleep(delay)
        pdf, _ = res
    except Exception as exc:
        ed.status = f"erro_download: {exc}"
        logger.error(ed.status)
        return
    if pdf is None:
        ed.status = "pdf_ausente_no_servidor"
        logger.warning("%s: 404 no espelho de PDFs — ver no DocReader (pede CAPTCHA)", ed.pdf_name)
        return

    try:
        # abre a partir dos bytes: se o PDF estiver corrompido, o PyMuPDF não fica
        # segurando o arquivo (no Windows isso impediria apagá-lo do cache)
        with pymupdf.open(stream=pdf.read_bytes(), filetype="pdf") as doc:
            pages = [page.get_text() for page in doc]
    except Exception as exc:  # PDF truncado/corrompido: descarta para baixar de novo na próxima vez
        ed.status = f"erro_pdf: {str(exc).splitlines()[0]}"
        logger.error("%s: não consegui ler o PDF (%s); arquivo removido do cache", pdf.name, ed.status)
        try:
            pdf.unlink(missing_ok=True)
        except OSError as unlink_exc:
            logger.warning("não consegui apagar %s (%s)", pdf, unlink_exc)
        return

    pub_date = publication_date(pages[0], ed.ano) if pages else None
    coletado = datetime.now(timezone.utc).isoformat()
    for i, text in enumerate(pages, start=1):
        match_text = None
        if person is not None:
            match_text = person["match"](text)
            if not match_text:
                continue
        elif not page_matches(text, needle):
            continue
        ed.paginas_pdf.append(i)
        yield Occurrence(
            id=make_occurrence_id(person["query"] if person else surname, ed.bib, ed.ano, ed.edicao, i),
            query=surname, bib=ed.bib, periodical=ed.jornal,
            year=ed.ano, edition=ed.edicao, physical_page=i,
            source_url=f"{ed.pdf_url}#page={i}", ocr_raw=text,
            collected_at=coletado, publication_date=pub_date,
            person_query=person["query"] if person else None,
            search_mode=person["mode"] if person else None,
            match_text=match_text,
        )
    ed.status = "ok" if ed.paginas_pdf else "nao_achado_no_texto_do_pdf"
