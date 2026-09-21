"""Coletor de edições da Hemeroteca Digital (Biblioteca Nacional).

Estratégia: os PDFs de cada edição ficam em `hemeroteca-pdf.bn.gov.br`, um
servidor estático sem sessão, cookies ou proteção anti-bot, no padrão:

    https://hemeroteca-pdf.bn.gov.br/<bib>/per<bib>_<ano>_<edicao5digitos>.pdf

Cada PDF já contém uma camada de texto pesquisável (OCR feito pela BN), então
não é necessário rodar OCR local nem automatizar navegador para buscar um
sobrenome — basta baixar o PDF e ler o texto embutido.

A lista de anos/edições de um `bib` (o índice) não está disponível por uma
requisição HTTP simples: a página que a contém, em bndigital.bn.gov.br, fica
atrás de um desafio anti-bot do Cloudflare. Esse índice precisa ser obtido
uma vez por periódico, manualmente, abrindo
`https://bndigital.bn.gov.br/acervo-digital/jornal/<bib>` num navegador e
salvando o HTML (Ctrl+U) ou copiando o objeto `jsonData` embutido na página.
Este módulo lê esse HTML/JSON via `PeriodicalIndex.from_html` ou
`PeriodicalIndex.from_json_file`.

Aviso: alguns caracteres acentuados (á, ã, ç, é...) saem corrompidos como
"�" na extração de texto por causa de um mapa de fonte malformado nos
PDFs de origem. Nomes próprios sem acento — o caso comum de sobrenomes —
extraem corretamente. A busca aqui ignora acentos dos dois lados para reduzir
o impacto do problema.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import httpx
import pymupdf

logger = logging.getLogger("collector.bn_client")

PDF_HOST = "https://hemeroteca-pdf.bn.gov.br"
USER_AGENT = "family-history-search/0.1 (pesquisa genealogica nao comercial)"

_EDITION_NUMBER_RE = re.compile(r"^(\d+)(.*)$")


def _normalize(text: str) -> str:
    """Remove acentos para comparação, mantendo o texto original intacto."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class Edition:
    """Uma edição de um periódico, identificada por bib/ano/número."""

    bib: str
    year: int
    number: str  # mantém sufixos como "91A"

    @property
    def filename(self) -> str:
        match = _EDITION_NUMBER_RE.match(self.number)
        if not match:
            raise ValueError(f"número de edição inesperado: {self.number!r}")
        digits, suffix = match.groups()
        return f"per{self.bib}_{self.year}_{digits.zfill(5)}{suffix}.pdf"

    @property
    def url(self) -> str:
        return f"{PDF_HOST}/{self.bib}/{self.filename}"


@dataclass(frozen=True)
class PeriodicalIndex:
    """Índice de edições de um periódico (bib -> lista de Edition)."""

    bib: str
    titulo: str
    editions: tuple[Edition, ...]

    @classmethod
    def from_json_data(cls, data: dict) -> "PeriodicalIndex":
        if data.get("meses"):
            raise NotImplementedError(
                "este índice usa granularidade mensal ('meses': true), "
                "que ainda não é suportada por PeriodicalIndex.from_json_data"
            )
        bib = str(data["bin"])
        editions = tuple(
            Edition(bib=bib, year=int(year), number=number)
            for year, numbers in data["anos"].items()
            for number in numbers
        )
        return cls(bib=bib, titulo=data.get("titulo", ""), editions=editions)

    @classmethod
    def from_json_file(cls, path: Path) -> "PeriodicalIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_json_data(data)

    @classmethod
    def from_html(cls, html: str) -> "PeriodicalIndex":
        match = re.search(r"var jsonData\s*=\s*'(.*?)';", html, re.DOTALL)
        if not match:
            raise ValueError("não encontrei 'var jsonData = ...' no HTML informado")
        data = json.loads(match.group(1))
        return cls.from_json_data(data)

    @classmethod
    def from_html_file(cls, path: Path) -> "PeriodicalIndex":
        return cls.from_html(Path(path).read_text(encoding="utf-8", errors="replace"))

    def save_json(self, path: Path) -> None:
        data = {
            "bin": self.bib,
            "titulo": self.titulo,
            "anos": _group_by_year(self.editions),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def filter_years(self, start_year: int | None, end_year: int | None) -> "PeriodicalIndex":
        editions = tuple(
            e
            for e in self.editions
            if (start_year is None or e.year >= start_year)
            and (end_year is None or e.year <= end_year)
        )
        return PeriodicalIndex(bib=self.bib, titulo=self.titulo, editions=editions)


def _group_by_year(editions: Iterable[Edition]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for edition in editions:
        grouped.setdefault(str(edition.year), []).append(edition.number)
    return grouped


@dataclass(frozen=True)
class Occurrence:
    """Uma página de uma edição onde o termo buscado aparece."""

    id: str
    query: str
    bib: str
    periodical: str
    year: int
    edition: str
    physical_page: int  # página dentro do PDF da edição (1-indexed)
    source_url: str
    ocr_raw: str
    collected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def download_edition(edition: Edition, cache_dir: Path, client: httpx.Client, *, delay: float = 0.0) -> Path:
    """Baixa o PDF de uma edição, usando cache local para evitar downloads repetidos."""
    dest = cache_dir / edition.bib / edition.filename
    if dest.exists():
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    response = client.get(edition.url)
    if response.status_code == 404:
        raise FileNotFoundError(f"edição não encontrada no servidor: {edition.url}")
    response.raise_for_status()

    tmp = dest.with_suffix(".pdf.part")
    tmp.write_bytes(response.content)
    tmp.rename(dest)

    if delay:
        time.sleep(delay)
    return dest


def extract_pages(pdf_path: Path) -> list[str]:
    """Retorna o texto de cada página do PDF (índice 0 = página física 1)."""
    with pymupdf.open(pdf_path) as doc:
        return [page.get_text() for page in doc]


def _iter_occurrences(
    editions: list[Edition],
    surname: str,
    cache_dir: Path,
    *,
    periodical: str,
    client: httpx.Client,
    limit: int | None,
    delay: float,
) -> Iterator[Occurrence]:
    needle = _normalize(surname)
    if not needle:
        raise ValueError("surname vazio")

    found = 0
    for edition in editions:
        if limit is not None and found >= limit:
            logger.info("limite de %d ocorrências atingido, parando", limit)
            return

        try:
            already_cached = (cache_dir / edition.bib / edition.filename).exists()
            pdf_path = download_edition(
                edition, cache_dir, client, delay=0.0 if already_cached else delay
            )
        except FileNotFoundError:
            logger.warning("edição ausente no servidor, ignorando: %s", edition.filename)
            continue
        except httpx.HTTPError as exc:
            logger.error("falha ao baixar %s: %s", edition.filename, exc)
            continue

        try:
            pages = extract_pages(pdf_path)
        except Exception as exc:  # arquivo corrompido/baixado incompleto
            logger.error("falha ao ler PDF %s: %s", pdf_path, exc)
            continue

        for page_number, text in enumerate(pages, start=1):
            if needle not in _normalize(text):
                continue
            found += 1
            yield Occurrence(
                id=str(uuid.uuid4()),
                query=surname,
                bib=edition.bib,
                periodical=periodical,
                year=edition.year,
                edition=edition.number,
                physical_page=page_number,
                source_url=f"{edition.url}#page={page_number}",
                ocr_raw=text,
                collected_at=datetime.now(timezone.utc).isoformat(),
            )
            if limit is not None and found >= limit:
                break

    logger.info("busca concluída: %d ocorrência(s) para %r", found, surname)


def search_surname(
    index: PeriodicalIndex,
    surname: str,
    cache_dir: Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    limit: int | None = None,
    delay: float = 1.5,
) -> Iterator[Occurrence]:
    """Baixa TODAS as edições do índice (filtradas por ano) e produz uma
    Occurrence para cada página cujo texto contém o sobrenome buscado.

    Use quando não se sabe de antemão em quais edições o termo aparece.
    Quando a BN já informou essas edições (ver `parse_hits_html`), prefira
    `fetch_known_occurrences` — é muito mais rápido, pois baixa só o
    necessário. Edições já baixadas não geram nova requisição de rede
    (cache em disco).
    """
    target = index.filter_years(start_year, end_year)
    editions = sorted(target.editions, key=lambda e: (e.year, e.number))
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        yield from _iter_occurrences(
            editions, surname, cache_dir, periodical=index.titulo, client=client, limit=limit, delay=delay
        )


_HIT_ROW_RE = re.compile(r">ano (\d+)\\edicao (\d+[\w]*)\s*<.*?<span>(\d+)</span>", re.DOTALL)
_HIT_ROW_RE_ALT = re.compile(
    r">per\w+?_(\d{4})_(\d+[\w]*)\s*<.*?<span>(\d+)</span>", re.DOTALL
)
_BIB_HIDDEN_RE = re.compile(r'name="bibhid"[^>]*value="([^"]*)"')


def parse_hits_html(html: str) -> tuple[str, list[Edition]]:
    """Lê uma página `ListaPastaOcor.aspx` salva do navegador — a lista de
    ocorrências de uma busca por um termo dentro de um periódico — e
    retorna o bib e a lista de edições onde o termo aparece.

    Essa página só é alcançável através de uma sessão de navegador ativa em
    memoria.bn.gov.br/DocReader (ex: abrindo o link de busca e salvando o
    HTML com Ctrl+U), mas já contém a lista final — sem precisar paginar
    ocorrência por ocorrência nem baixar o acervo inteiro.

    A "pasta" de cada ocorrência aparece em dois formatos dependendo do
    periódico: `ano <ano>\\edicao <numero>` ou, em alguns periódicos mais
    novos, diretamente `per<bib>_<ano>_<numero>` (o próprio prefixo do
    arquivo). Os dois são reconhecidos aqui.
    """
    bib_match = _BIB_HIDDEN_RE.search(html)
    if not bib_match:
        raise ValueError("não encontrei o campo 'bibhid' no HTML informado")
    bib = bib_match.group(1)
    editions = [
        Edition(bib=bib, year=int(year), number=number)
        for year, number, _count in _HIT_ROW_RE.findall(html)
    ]
    if not editions:
        editions = [
            Edition(bib=bib, year=int(year), number=number)
            for year, number, _count in _HIT_ROW_RE_ALT.findall(html)
        ]
    return bib, editions


def fetch_known_occurrences(
    editions: list[Edition],
    surname: str,
    cache_dir: Path,
    *,
    periodical: str = "",
    limit: int | None = None,
    delay: float = 1.5,
) -> Iterator[Occurrence]:
    """Baixa e extrai apenas as edições informadas (por exemplo, a saída de
    `parse_hits_html`), sem precisar do índice completo do periódico.
    """
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        yield from _iter_occurrences(
            editions, surname, cache_dir, periodical=periodical, client=client, limit=limit, delay=delay
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Busca um sobrenome nas edições de um periódico da Hemeroteca Digital (BN)."
    )
    parser.add_argument("--surname", required=True, help="sobrenome a buscar, ex: Effting")
    parser.add_argument("--bib", required=True, help="id do periódico, ex: 886173")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--index-html",
        type=Path,
        help=(
            "HTML salvo de bndigital.bn.gov.br/acervo-digital/jornal/<bib> (Ctrl+U no navegador); "
            "baixa e verifica TODAS as edições do periódico"
        ),
    )
    source_group.add_argument(
        "--index-json",
        type=Path,
        help="índice já extraído em JSON (ver PeriodicalIndex.save_json); mesmo comportamento de --index-html",
    )
    source_group.add_argument(
        "--hits-html",
        type=Path,
        help=(
            "HTML salvo de ListaPastaOcor.aspx (resultado de uma busca dentro do DocReader; Ctrl+U no navegador); "
            "baixa só as edições que a BN já indicou como tendo o termo — muito mais rápido"
        ),
    )
    parser.add_argument(
        "--titulo",
        default="",
        help="nome do periódico, usado apenas com --hits-html (essa página não traz o título)",
    )
    parser.add_argument("--start-year", type=int, default=None, help="ignorado com --hits-html")
    parser.add_argument("--end-year", type=int, default=None, help="ignorado com --hits-html")
    parser.add_argument("--limit", type=int, default=None, help="máximo de ocorrências a coletar")
    parser.add_argument("--delay", type=float, default=1.5, help="segundos entre downloads (padrão: 1.5)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="diretório base de dados (padrão: ./data)")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)

    raw_dir = args.data_dir / "raw"

    if args.hits_html:
        html = args.hits_html.read_text(encoding="utf-8", errors="replace")
        bib, editions = parse_hits_html(html)
        if bib != args.bib:
            logger.warning(
                "bib do HTML de ocorrências (%s) difere do --bib informado (%s); usando o do HTML",
                bib,
                args.bib,
            )
        (raw_dir / bib).mkdir(parents=True, exist_ok=True)
        (raw_dir / bib / f"hits_{_normalize(args.surname)}.json").write_text(
            json.dumps([{"ano": e.year, "edicao": e.number} for e in editions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        occurrences = list(
            fetch_known_occurrences(
                editions,
                args.surname,
                cache_dir=raw_dir,
                periodical=args.titulo,
                limit=args.limit,
                delay=args.delay,
            )
        )
    else:
        if args.index_html:
            index = PeriodicalIndex.from_html_file(args.index_html)
        else:
            index = PeriodicalIndex.from_json_file(args.index_json)

        if index.bib != args.bib:
            logger.warning(
                "bib do índice (%s) difere do --bib informado (%s); usando o do índice",
                index.bib,
                args.bib,
            )

        bib = index.bib
        index.save_json(raw_dir / bib / "index.json")

        occurrences = list(
            search_surname(
                index,
                args.surname,
                cache_dir=raw_dir,
                start_year=args.start_year,
                end_year=args.end_year,
                limit=args.limit,
                delay=args.delay,
            )
        )

    processed_dir = args.data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"occurrences_{bib}_{_normalize(args.surname)}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for occurrence in occurrences:
            f.write(json.dumps(occurrence.to_dict(), ensure_ascii=False) + "\n")

    logger.info("%d ocorrência(s) salvas em %s", len(occurrences), out_path)


if __name__ == "__main__":
    main()
