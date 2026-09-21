"""Catálogo de periódicos da Hemeroteca Digital (BN), via docmulti.aspx.

`memoria.bn.gov.br/DocReader/docmulti.aspx?bib=[cache]<chave>&pesq=<termo>`
é uma página de resultados "amplos" (ex: todos os periódicos de um estado)
que a BN gera ao fazer certas buscas — mostra uma grade paginada (50 por
página) com o nome e o `bib` de cada periódico. Essa página funciona sem
Cloudflare, mas o cache `[cache]<chave>` está preso à sessão do navegador
que o gerou, então este módulo precisa de um cookie de sessão válido (ver
`fetch_catalog` / CLI abaixo) — normalmente obtido copiando o cookie do
DevTools depois de abrir esse tipo de link uma vez no navegador.

Uma vez capturado, o catálogo inteiro (ex: os 1051 periódicos de SC) é
obtido automaticamente, paginando via postback assíncrono do RadGrid.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from src.collector.ms_ajax_delta import get_hidden_field, get_update_panel

logger = logging.getLogger("collector.catalog")

USER_AGENT = "family-history-search/0.1 (pesquisa genealogica nao comercial)"
GRID_PANEL_ID = "ListaRadGridPanel"

_ROW_RE = re.compile(
    r'<td align="left" style="width:295px;">([^<]*)</td>.*?showMenu\(event,"([^"]+)",',
    re.DOTALL,
)
_PAGE_INFO_RE = re.compile(r"p[aá]gina (\d+) de (\d+)")
_NEXT_BTN_RE = re.compile(r'name="([^"]+)"[^>]*title="Pr[oó]xima p[aá]gina"')
_FIRST_BTN_RE = re.compile(r'name="([^"]+)"[^>]*title="Primeira p[aá]gina"')


@dataclass(frozen=True)
class CatalogEntry:
    bib: str
    titulo: str


def _parse_rows(panel_html: str) -> list[CatalogEntry]:
    return [
        CatalogEntry(bib=bib, titulo=nome.strip())
        for nome, bib in _ROW_RE.findall(panel_html)
    ]


def fetch_catalog(
    seed_post_body: str,
    docmulti_url: str,
    cookie: str,
    *,
    delay: float = 1.0,
    max_pages: int = 200,
) -> list[CatalogEntry]:
    """Baixa o catálogo completo de periódicos referenciado por uma URL
    `docmulti.aspx?bib=[cache]...&pesq=...` — paginando automaticamente.

    Esse cache não aceita uma requisição "fria" (uma tentativa de GET direto
    nele deu 500 — parece exigir que a sessão já tenha navegado até ali pelo
    fluxo normal). Por isso a função exige `seed_post_body`: o corpo bruto
    de UM postback real, copiado do DevTools (aba Network → clique num
    número de página → aba Payload/Request → "view source"/raw) depois de
    abrir esse link no navegador uma vez. A partir dele, o resto das
    páginas é buscado automaticamente.

    `cookie` é o cabeçalho `Cookie` completo copiado do navegador (DevTools
    → Network → a mesma requisição → Request Headers).
    """
    parsed = urllib.parse.urlparse(docmulti_url)
    qs = urllib.parse.parse_qs(parsed.query)
    bib = qs["bib"][0]
    pesq = qs.get("pesq", [""])[0]
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": cookie,
        "Referer": docmulti_url,
        "Origin": f"{parsed.scheme}://{parsed.netloc}",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-MicrosoftAjax": "Delta=true",
        "X-Requested-With": "XMLHttpRequest",
    }
    client = httpx.Client(headers=headers, timeout=30.0)

    hidden_fields: dict[str, str] = {}
    for part in seed_post_body.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        hidden_fields[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(value)

    entries_by_bib: dict[str, str] = {}

    def do_postback(event_target: str) -> str:
        fields = dict(hidden_fields)
        fields["__EVENTTARGET"] = event_target
        fields["ScriptManager1"] = f"{GRID_PANEL_ID}|{event_target}"
        fields["__ASYNCPOST"] = "true"
        body = "&".join(
            f"{urllib.parse.quote_plus(k)}={urllib.parse.quote_plus(v)}"
            for k, v in fields.items()
        )
        # O servidor às vezes responde "0|error|500||" sem motivo aparente
        # sob requisições repetidas — transitório, uma segunda tentativa
        # com um respiro geralmente resolve.
        for attempt in range(3):
            r = client.post(base_url, params={"bib": bib, "pesq": pesq}, content=body)
            r.raise_for_status()
            text = r.content.decode("utf-8")
            if not text.startswith("0|error|"):
                return text
            logger.warning("erro transitório do servidor (tentativa %d/3), aguardando", attempt + 1)
            time.sleep(delay * 3)
        return text

    # A primeira chamada usa o __EVENTTARGET já presente no seed capturado
    # (o que a página estava prestes a clicar quando foi salva).
    seed_target = hidden_fields.get("__EVENTTARGET", "")
    text = do_postback(seed_target) if seed_target else ""
    panel = get_update_panel(text, GRID_PANEL_ID) if text else None
    page_html = panel or ""
    if panel:
        for entry in _parse_rows(panel):
            entries_by_bib[entry.bib] = entry.titulo
        new_vs = get_hidden_field(text, "__VIEWSTATE")
        new_vsg = get_hidden_field(text, "__VIEWSTATEGENERATOR")
        if new_vs:
            hidden_fields["__VIEWSTATE"] = new_vs
        if new_vsg:
            hidden_fields["__VIEWSTATEGENERATOR"] = new_vsg

    # Sempre busca a primeira página também (a busca costuma cair direto no meio da lista).
    first_btn = _FIRST_BTN_RE.search(page_html)
    if first_btn:
        text = do_postback(first_btn.group(1))
        panel = get_update_panel(text, GRID_PANEL_ID)
        if panel:
            for entry in _parse_rows(panel):
                entries_by_bib[entry.bib] = entry.titulo
            new_vs = get_hidden_field(text, "__VIEWSTATE")
            new_vsg = get_hidden_field(text, "__VIEWSTATEGENERATOR")
            if new_vs:
                hidden_fields["__VIEWSTATE"] = new_vs
            if new_vsg:
                hidden_fields["__VIEWSTATEGENERATOR"] = new_vsg
            page_html = panel  # para achar o botão "próxima página" abaixo

    for _ in range(max_pages):
        next_match = _NEXT_BTN_RE.search(page_html)
        if not next_match:
            break
        time.sleep(delay)
        text = do_postback(next_match.group(1))
        panel = get_update_panel(text, GRID_PANEL_ID)
        if not panel:
            logger.warning("resposta sem o painel da grade, parando")
            break
        for entry in _parse_rows(panel):
            entries_by_bib[entry.bib] = entry.titulo

        page_match = _PAGE_INFO_RE.search(panel)
        if page_match:
            logger.info(
                "página %s de %s — %d periódicos acumulados",
                page_match.group(1), page_match.group(2), len(entries_by_bib),
            )

        new_vs = get_hidden_field(text, "__VIEWSTATE")
        new_vsg = get_hidden_field(text, "__VIEWSTATEGENERATOR")
        if not new_vs:
            break
        hidden_fields["__VIEWSTATE"] = new_vs
        if new_vsg:
            hidden_fields["__VIEWSTATEGENERATOR"] = new_vsg
        page_html = panel

    return [CatalogEntry(bib=b, titulo=t) for b, t in entries_by_bib.items()]


def save_catalog(entries: list[CatalogEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Baixa o catálogo de periódicos de um link docmulti.aspx?bib=[cache]...",
    )
    parser.add_argument("--url", required=True, help="URL completa do docmulti.aspx")
    parser.add_argument("--cookie", required=True, help="cabeçalho Cookie copiado do navegador")
    parser.add_argument(
        "--seed",
        type=Path,
        required=True,
        help="arquivo com o corpo bruto (raw) de um POST real capturado no DevTools",
    )
    parser.add_argument("--out", type=Path, required=True, help="arquivo JSON de saída")
    parser.add_argument("--delay", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)
    seed_post_body = args.seed.read_text(encoding="utf-8")
    entries = fetch_catalog(seed_post_body, args.url, args.cookie, delay=args.delay)
    save_catalog(entries, args.out)
    logger.info("%d periódicos salvos em %s", len(entries), args.out)


if __name__ == "__main__":
    main()
