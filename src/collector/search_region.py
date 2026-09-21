"""Busca um sobrenome em todos os periódicos de uma região de uma vez.

Combina três descobertas sobre o `docmulti.aspx` do DocReader:

1. A grade de periódicos (mesma usada por `catalog.py`) tem uma coluna
   "Ocorrências" real — só não aparece populada na ordem padrão (a maior
   parte dos periódicos tem 0 ocorrências, então uma amostra aleatória
   raramente encontra um valor diferente de zero). Ordenada decrescente por
   essa coluna, a primeira página já traz TODOS os periódicos com pelo
   menos 1 ocorrência, com a contagem exata.
2. O campo oculto `HiddenID` dessa mesma sessão é literalmente o
   `SessaoID` que `ListaPastaOcor.aspx?bib=<bib>&SessaoID=<...>&pesq=<...>`
   espera — então, tendo isso uma vez, dá para montar essa URL para
   QUALQUER `bib`, via GET simples, sem precisar abrir cada periódico no
   navegador.
3. `ListaPastaOcor.aspx` lista as edições exatas onde o termo aparece
   (formato de "pasta" varia entre periódicos — `bn_client.parse_hits_html`
   já trata as variações conhecidas).

Ou seja: uma única captura manual (a grade ordenada por Ocorrências
decrescente, mais o cookie de sessão) é suficiente para buscar o termo em
TODOS os periódicos que tiverem alguma ocorrência, sem mais nenhuma
interação manual por periódico.

Limitação conhecida: o espelho de PDFs (`hemeroteca-pdf.bn.gov.br`) não tem
100% das edições que o índice de busca da BN conhece — algumas edições
(sobretudo de periódicos muito grandes, com dezenas de milhares de páginas)
dão 404. Isso é uma lacuna do lado da BN, não deste código; fica registrado
nos logs como "edição ausente no servidor".
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.collector.bn_client import USER_AGENT, fetch_known_occurrences, parse_hits_html

logger = logging.getLogger("collector.search_region")

_ROW_RE = re.compile(
    r'<td align="left" style="width:295px;">([^<]*)</td>'
    r'<td align="center" style="font-weight:bold;width:105px;">(\d+)</td>'
    r'<td align="center" style="font-weight:bold;width:105px;">(\d+)</td>'
    r'.*?showMenu\(event,"([^"]+)",',
    re.DOTALL,
)


@dataclass(frozen=True)
class PeriodicalHits:
    bib: str
    titulo: str
    ocorrencias_bn: int


def parse_ranked_grid(html_or_delta: str) -> list[PeriodicalHits]:
    """Extrai periódicos com ocorrência > 0 da grade do docmulti.aspx,
    capturada com a coluna Ocorrências ordenada em ordem decrescente
    (clique no botão "Decrescente" do cabeçalho da coluna no navegador).
    """
    rows = _ROW_RE.findall(html_or_delta)
    return [
        PeriodicalHits(bib=bib, titulo=nome.strip(), ocorrencias_bn=int(ocor))
        for nome, _paginas, ocor, bib in rows
        if int(ocor) > 0
    ]


def search_region(
    periodicals: list[PeriodicalHits],
    surname: str,
    sessao_id: str,
    pesq: str,
    cookie: str,
    cache_dir: Path,
    *,
    delay: float = 1.0,
):
    """Para cada periódico com ocorrência, busca as edições exatas
    (`ListaPastaOcor.aspx`) e produz as Occurrence de todos eles.
    """
    headers = {"User-Agent": USER_AGENT, "Cookie": cookie}
    client = httpx.Client(headers=headers, timeout=30.0)

    for p in periodicals:
        r = client.get(
            "https://memoria.bn.gov.br/DocReader/ListaPastaOcor.aspx",
            params={"bib": p.bib, "SessaoID": sessao_id, "pesq": pesq},
        )
        r.raise_for_status()
        try:
            _, editions = parse_hits_html(r.text)
        except ValueError as exc:
            logger.error("falha ao ler ListaPastaOcor de %s (%s): %s", p.bib, p.titulo, exc)
            continue

        if not editions:
            logger.warning(
                "%s (%s): BN reportou %d ocorrência(s) mas não achei a lista de edições",
                p.bib, p.titulo, p.ocorrencias_bn,
            )

        yield from fetch_known_occurrences(
            editions, surname, cache_dir, periodical=p.titulo, delay=delay
        )
        time.sleep(delay)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Busca um sobrenome em todos os periódicos com ocorrência de uma região.",
    )
    parser.add_argument("--surname", required=True)
    parser.add_argument(
        "--ranked-grid",
        type=Path,
        required=True,
        help="arquivo com a resposta do docmulti.aspx ordenada por Ocorrências decrescente",
    )
    parser.add_argument("--sessao-id", required=True, help="valor do campo oculto HiddenID/SessaoID")
    parser.add_argument("--pesq", required=True, help='termo exatamente como foi buscado, ex: "Effting"')
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--delay", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)

    grid_html = args.ranked_grid.read_text(encoding="utf-8")
    periodicals = parse_ranked_grid(grid_html)
    logger.info("%d periódico(s) com ocorrência encontrados na grade", len(periodicals))

    raw_dir = args.data_dir / "raw"
    processed_dir = args.data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"occurrences_region_{args.surname.lower()}.jsonl"

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for occ in search_region(
            periodicals, args.surname, args.sessao_id, args.pesq, args.cookie,
            raw_dir, delay=args.delay,
        ):
            f.write(json.dumps(occ.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            count += 1

    logger.info("%d ocorrência(s) salvas em %s", count, out_path)


if __name__ == "__main__":
    main()
