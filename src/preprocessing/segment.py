"""Recorte de contexto ao redor do sobrenome, com posição na página e
sinalização de possível mistura de colunas (Fase 2).

Reabre o PDF já em cache local (baixado na Fase 1) e usa os blocos de
texto do PyMuPDF — que trazem posição (bounding box) — em vez do texto
plano de `ocr_raw`, para conseguir: localizar o sobrenome de forma exata
e aproximada, recortar só o bloco relevante (não a página toda), guardar
onde ele está, e detectar quando outro bloco ocupa a mesma faixa vertical
mas uma faixa horizontal diferente (colunas lado a lado — sinal de que a
ordem de leitura usada em `ocr_raw` pode intercalar as duas).
"""

from __future__ import annotations

import argparse
import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from rapidfuzz import fuzz

from src.collector.bn_client import Edition
from src.preprocessing.normalize import normalize_text

logger = logging.getLogger("preprocessing.segment")

FUZZY_THRESHOLD = 85  # 0-100, rapidfuzz.fuzz.ratio
CONTEXT_FALLBACK_CHARS = 600
MIN_EXCERPT_CHARS = 80  # abaixo disso, busca um bloco vizinho pra dar contexto

Bbox = tuple[float, float, float, float]


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class ContextMatch:
    context_excerpt: str
    match_type: str  # "exata" ou "aproximada"
    match_score: float  # 1.0 para exata; 0-1 para aproximada
    context_bbox: Bbox
    column_mix_suspected: bool


def _overlap_vertical(a: Bbox, b: Bbox, min_fraction: float = 0.3) -> bool:
    _, ay0, _, ay1 = a
    _, by0, _, by1 = b
    overlap = min(ay1, by1) - max(ay0, by0)
    if overlap <= 0:
        return False
    shortest = min(ay1 - ay0, by1 - by0)
    return shortest > 0 and (overlap / shortest) >= min_fraction


def _overlap_horizontal(a: Bbox, b: Bbox) -> bool:
    ax0, _, ax1, _ = a
    bx0, _, bx1, _ = b
    return max(ax0, bx0) < min(ax1, bx1)


def _find_matching_blocks(blocks: list[tuple], surname: str) -> list[tuple[Bbox, str, str, float]]:
    needle = _strip_accents(surname)
    matches = []
    for x0, y0, x1, y1, block_text, *_ in blocks:
        norm_block = _strip_accents(block_text)
        if needle in norm_block:
            matches.append(((x0, y0, x1, y1), block_text, "exata", 1.0))
            continue
        best_score = 0.0
        for word in norm_block.split():
            score = fuzz.ratio(needle, word)
            if score > best_score:
                best_score = score
        if best_score >= FUZZY_THRESHOLD:
            matches.append(((x0, y0, x1, y1), block_text, "aproximada", best_score / 100.0))
    return matches


def _nearest_neighbor(bbox: Bbox, blocks: list[tuple], *, above: bool) -> tuple[Bbox, str] | None:
    """Bloco mais próximo na mesma coluna (mesma faixa horizontal),
    imediatamente acima ou abaixo de `bbox`.
    """
    best: tuple[float, Bbox, str] | None = None
    for x0, y0, x1, y1, text, *_ in blocks:
        other = (x0, y0, x1, y1)
        if other == bbox or not _overlap_horizontal(bbox, other):
            continue
        distance = bbox[1] - y1 if above else y0 - bbox[3]
        if distance < 0:
            continue
        if best is None or distance < best[0]:
            best = (distance, other, text)
    return (best[1], best[2]) if best else None


def _widen_short_excerpt(bbox: Bbox, block_text: str, blocks: list[tuple]) -> tuple[Bbox, str]:
    """Se o bloco encontrado for muito curto (comum em legendas e colunas
    sociais, ex: só o nome), junta o vizinho acima e/ou abaixo na mesma
    coluna para dar algum contexto de que fala o trecho.
    """
    if len(block_text.strip()) >= MIN_EXCERPT_CHARS:
        return bbox, block_text

    x0, y0, x1, y1 = bbox
    parts = [block_text]
    before = _nearest_neighbor(bbox, blocks, above=True)
    if before:
        (bx0, by0, bx1, by1), before_text = before
        parts.insert(0, before_text)
        x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
    after = _nearest_neighbor(bbox, blocks, above=False)
    if after:
        (ax0, ay0, ax1, ay1), after_text = after
        parts.append(after_text)
        x0, y0, x1, y1 = min(x0, ax0), min(y0, ay0), max(x1, ax1), max(y1, ay1)
    return (x0, y0, x1, y1), "\n".join(parts)


def _column_mix_suspected(bbox: Bbox, blocks: list[tuple]) -> bool:
    for x0, y0, x1, y1, *_ in blocks:
        other = (x0, y0, x1, y1)
        if other == bbox:
            continue
        if _overlap_vertical(bbox, other) and not _overlap_horizontal(bbox, other):
            return True
    return False


def find_context(pdf_path: Path, physical_page: int, surname: str) -> ContextMatch | None:
    """Localiza o(s) bloco(s) de texto que contêm `surname` na página
    indicada (1-indexed) e retorna o recorte de contexto correspondente,
    com posição e sinalização de mistura de colunas.

    Retorna None se o sobrenome não foi encontrado em nenhum bloco da
    página — quem chama deve então cair para o texto integral da página.
    """
    with pymupdf.open(pdf_path) as doc:
        page = doc[physical_page - 1]
        blocks = page.get_text("blocks")

    matches = _find_matching_blocks(blocks, surname)
    if not matches:
        return None

    # prioriza o match exato quando houver mais de um bloco candidato
    matches.sort(key=lambda m: (m[2] != "exata", -m[3]))
    bbox, block_text, match_type, score = matches[0]
    column_mix = _column_mix_suspected(bbox, blocks)
    widened_bbox, widened_text = _widen_short_excerpt(bbox, block_text, blocks)

    return ContextMatch(
        context_excerpt=normalize_text(widened_text),
        match_type=match_type,
        match_score=score,
        context_bbox=widened_bbox,
        column_mix_suspected=column_mix,
    )


def enrich_occurrence(occurrence: dict, cache_dir: Path) -> dict:
    """Recebe um dict de Occurrence (Fase 1, de `bn_client`) e retorna uma
    cópia com os campos da Fase 2: `text_normalized`, `context_excerpt`,
    `match_type`, `match_score`, `context_bbox` e `column_mix_suspected`.

    `ocr_raw` nunca é alterado. Quando o PDF de origem não está mais em
    cache ou o sobrenome não é localizado num bloco específico, o recorte
    cai para os primeiros caracteres da página normalizada e `match_type`
    fica "indeterminado".
    """
    enriched = dict(occurrence)
    text_normalized = normalize_text(occurrence["ocr_raw"])
    enriched["text_normalized"] = text_normalized

    edition = Edition(bib=occurrence["bib"], year=occurrence["year"], number=occurrence["edition"])
    pdf_path = cache_dir / edition.bib / edition.filename

    match: ContextMatch | None = None
    if pdf_path.exists():
        try:
            match = find_context(pdf_path, occurrence["physical_page"], occurrence["query"])
        except Exception as exc:  # PDF corrompido, página fora do intervalo, etc.
            logger.warning("falha ao recortar contexto de %s pág. %s: %s", pdf_path, occurrence["physical_page"], exc)
    else:
        logger.warning("PDF não encontrado em cache: %s", pdf_path)

    if match is None:
        enriched["context_excerpt"] = text_normalized[:CONTEXT_FALLBACK_CHARS]
        enriched["match_type"] = "indeterminado"
        enriched["match_score"] = None
        enriched["context_bbox"] = None
        enriched["column_mix_suspected"] = None
    else:
        enriched["context_excerpt"] = match.context_excerpt
        enriched["match_type"] = match.match_type
        enriched["match_score"] = match.match_score
        enriched["context_bbox"] = list(match.context_bbox)
        enriched["column_mix_suspected"] = match.column_mix_suspected

    return enriched


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fase 2: normaliza e recorta o contexto das ocorrências coletadas na Fase 1."
    )
    parser.add_argument("--in", dest="input", type=Path, required=True, help="occurrences_*.jsonl da Fase 1")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="onde está o cache de PDFs (data/raw)")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)
    cache_dir = args.data_dir / "raw"

    total = 0
    indeterminado = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding="utf-8") as fin, args.out.open("w", encoding="utf-8") as fout:
        for line in fin:
            occurrence = json.loads(line)
            enriched = enrich_occurrence(occurrence, cache_dir)
            if enriched["match_type"] == "indeterminado":
                indeterminado += 1
            fout.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            total += 1

    ok_pct = 100 * (total - indeterminado) / total if total else 0.0
    logger.info(
        "%d ocorrência(s) processadas — %d com bloco localizado (%.1f%%), %d indeterminadas — salvo em %s",
        total, total - indeterminado, ok_pct, indeterminado, args.out,
    )


if __name__ == "__main__":
    main()
