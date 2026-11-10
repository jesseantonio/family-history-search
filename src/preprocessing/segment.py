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
from functools import lru_cache
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


@lru_cache(maxsize=None)
def _decompose_char(ch: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))


def _strip_accents(text: str) -> str:
    # Decompor letra a letra (com cache) dá o mesmo que NFKD do texto inteiro: a única
    # diferença do NFKD inteiro é reordenar marcas combinantes, e elas são descartadas.
    # O lower() fica no texto todo (o sigma final do grego depende do contexto).
    if text.isascii():
        return text.lower()
    return "".join(map(_decompose_char, text)).lower()


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
    """`surname` pode ser só o sobrenome ou um nome inteiro ("Carlo Barleta", na busca de
    pessoa); espaços e quebras de linha são comparados de forma igual."""
    needle = " ".join(_strip_accents(surname).split())
    multiword = " " in needle
    matches = []
    for x0, y0, x1, y1, block_text, *_ in blocks:
        norm_block = " ".join(_strip_accents(block_text).replace("-\n", "").split())
        if needle in norm_block:
            matches.append(((x0, y0, x1, y1), block_text, "exata", 1.0))
            continue
        if multiword:
            best_score = fuzz.partial_ratio(needle, norm_block) if len(norm_block) >= len(needle) else 0.0
        else:
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


class _PdfCache:
    """Mantém aberto o último PDF lido: as ocorrências chegam agrupadas por edição
    (várias páginas do mesmo PDF em sequência), e abrir o arquivo custa mais que ler
    a página. Use como `with _PdfCache() as pdfs:`."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._doc = None

    def get(self, path: Path):
        if path != self._path:
            self.close()
            self._doc = pymupdf.open(path)
            self._path = path
        return self._doc

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
        self._path, self._doc = None, None

    def __enter__(self) -> "_PdfCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def find_context(pdf_path: Path, physical_page: int, surname: str, *,
                 pdfs: _PdfCache | None = None) -> ContextMatch | None:
    """Localiza o(s) bloco(s) de texto que contêm `surname` na página
    indicada (1-indexed) e retorna o recorte de contexto correspondente,
    com posição e sinalização de mistura de colunas.

    Retorna None se o sobrenome não foi encontrado em nenhum bloco da
    página — quem chama deve então cair para o texto integral da página.
    `pdfs`, se informado, reaproveita o PDF aberto entre chamadas.
    """
    if pdfs is not None:
        blocks = pdfs.get(pdf_path)[physical_page - 1].get_text("blocks")
    else:
        with pymupdf.open(pdf_path) as doc:
            blocks = doc[physical_page - 1].get_text("blocks")

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


WIDE_CONTEXT_CHARS = 1400   # trecho ampliado mostrado na interface
WIDE_CONTEXT_MAX_GAP = 45   # pontos: um vão maior que isso na coluna costuma ser outra matéria


def _looks_like_heading(text: str) -> bool:
    """Título de matéria: curto e quase todo em maiúsculas ("FESTA", "NOTAS SOCIAIS")."""
    t = " ".join(text.split())
    letters_ = [c for c in t if c.isalpha()]
    return 0 < len(t) <= 60 and len(letters_) >= 3 and sum(c.isupper() for c in letters_) / len(letters_) >= 0.8


def wide_context(pdf_path: Path, physical_page: int, surname: str,
                 target_chars: int = WIDE_CONTEXT_CHARS) -> list[str] | None:
    """Trecho maior para leitura: o bloco com o sobrenome mais os blocos vizinhos da mesma
    coluna, acima e abaixo, alternando, até ~`target_chars` — sem atravessar para outra
    coluna nem pular um vão grande (outra matéria). Devolve os parágrafos na ordem de
    leitura, ou None se o sobrenome não foi achado em nenhum bloco da página."""
    with pymupdf.open(pdf_path) as doc:
        blocks = doc[physical_page - 1].get_text("blocks")
    matches = _find_matching_blocks(blocks, surname)
    if not matches:
        return None
    matches.sort(key=lambda m: (m[2] != "exata", -m[3]))
    bbox, text, _, _ = matches[0]
    column = bbox
    above, below = [], []
    top, bottom = bbox, bbox
    total = len(text)
    used = {bbox}
    stopped: set[str] = set()
    for _ in range(40):
        if total >= target_chars:
            break
        grew = False
        for direction in ("above", "below"):
            if direction in stopped:
                continue
            edge = top if direction == "above" else bottom
            best = None
            for x0, y0, x1, y1, t, *_ in blocks:
                other = (x0, y0, x1, y1)
                if other in used or not t.strip() or not _overlap_horizontal(column, other):
                    continue
                gap = edge[1] - y1 if direction == "above" else y0 - edge[3]
                if -2 <= gap <= WIDE_CONTEXT_MAX_GAP and (best is None or gap < best[0]):
                    best = (gap, other, t)
            if best is None:
                continue
            _, other, t = best
            used.add(other)
            if direction == "below" and _looks_like_heading(t):
                stopped.add("below")          # título abaixo: começa outra matéria
                continue
            total += len(t)
            grew = True
            if direction == "above":
                above.insert(0, t)
                top = other
                if _looks_like_heading(t):
                    stopped.add("above")      # título acima: é o começo desta matéria
            else:
                below.append(t)
                bottom = other
            if total >= target_chars:
                break
        if not grew:
            break
    return _paragraphs([normalize_text(t) for t in above + [text] + below])


def _paragraphs(parts: list[str]) -> list[str]:
    """Junta blocos que são linhas soltas da mesma frase (o OCR às vezes devolve cada
    linha impressa como um bloco), desfazendo a hifenização entre elas ("fi-" + "lhas")."""
    out: list[str] = []
    for p in filter(None, parts):
        if not out or _looks_like_heading(p) or _looks_like_heading(out[-1]):
            out.append(p)
            continue
        prev = out[-1]
        ends_sentence = prev.rstrip().endswith((".", "!", "?", ":", "»", '"', "”")) and p[:1].isupper()
        if ends_sentence and len(prev) > 160:
            out.append(p)
        elif prev.endswith("-") and p[:1].islower():
            out[-1] = prev[:-1] + p
        else:
            out[-1] = prev + " " + p
    return out


@lru_cache(maxsize=None)
def _fold_char(ch: str) -> str:
    folded = _strip_accents(ch)
    return folded if len(folded) == 1 else ch


def _fold_same_length(text: str) -> str:
    """Sem acento e minúsculo, caractere a caractere: posições continuam valendo no original."""
    if text.isascii():          # caso comum: nada a tirar, e lower() de ASCII não muda o tamanho
        return text.lower()
    return "".join(map(_fold_char, text))


def _window_around(text: str, surname: str, size: int = CONTEXT_FALLBACK_CHARS) -> str | None:
    """Plano B quando o PDF não ajuda a achar o bloco: recorta `size` caracteres do texto
    da página centrados na primeira ocorrência do sobrenome, cortando em fronteira de
    palavra. None se o sobrenome não aparece no texto (aí só resta o início da página)."""
    pos = _fold_same_length(text).find(_strip_accents(surname).strip())
    if pos < 0:
        return None
    start = max(0, pos - size // 2)
    end = min(len(text), start + size)
    start = max(0, end - size)
    if start > 0:
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < pos else start
    if end < len(text):
        space = text.rfind(" ", pos, end)
        end = space if space > pos else end
    return text[start:end].strip()


def _cached_pdf_path(occurrence: dict, cache_dir: Path) -> Path:
    """Caminho do PDF em cache. O nome vem do `source_url` (é o arquivo que foi
    baixado de fato), pois há edições sem número, como "revista"; só cai para a
    regra de `Edition.filename` em ocorrências antigas sem URL."""
    source_url = occurrence.get("source_url") or ""
    name = source_url.split("#", 1)[0].rsplit("/", 1)[-1]
    if not name.endswith(".pdf"):
        edition = Edition(bib=occurrence["bib"], year=occurrence["year"], number=occurrence["edition"])
        name = edition.filename
    return cache_dir / str(occurrence["bib"]) / name


def enrich_occurrence(occurrence: dict, cache_dir: Path, *, pdfs: _PdfCache | None = None) -> dict:
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

    pdf_path = _cached_pdf_path(occurrence, cache_dir)

    # busca de pessoa: recorta em volta do nome como foi lido nesta página ("Carlo Barleta"),
    # não do primeiro sobrenome da página, que pode ser de outra pessoa da família
    needle = occurrence.get("match_text") or occurrence["query"]
    match: ContextMatch | None = None
    if pdf_path.exists():
        try:
            match = find_context(pdf_path, occurrence["physical_page"], needle, pdfs=pdfs)
        except Exception as exc:  # PDF corrompido, página fora do intervalo, etc.
            if pdfs is not None:
                pdfs.close()      # não reaproveita um PDF que deu erro
            logger.warning("falha ao recortar contexto de %s pág. %s: %s", pdf_path, occurrence["physical_page"], exc)
    else:
        logger.warning("PDF não encontrado em cache: %s", pdf_path)

    if match is None:
        window = _window_around(text_normalized, needle) or _window_around(text_normalized, occurrence["query"])
        enriched["context_excerpt"] = window if window is not None else text_normalized[:CONTEXT_FALLBACK_CHARS]
        enriched["match_type"] = "janela_no_texto" if window is not None else "indeterminado"
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
    with args.input.open(encoding="utf-8") as fin, args.out.open("w", encoding="utf-8") as fout, \
            _PdfCache() as pdfs:
        for line in fin:
            occurrence = json.loads(line)
            enriched = enrich_occurrence(occurrence, cache_dir, pdfs=pdfs)
            if enriched["match_type"] == "indeterminado":
                indeterminado += 1
            fout.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            total += 1

    ok_pct = 100 * (total - indeterminado) / total if total else 0.0
    logger.info(
        "%d ocorrência(s) processadas — %d com trecho localizado (%.1f%%), %d indeterminadas — salvo em %s",
        total, total - indeterminado, ok_pct, indeterminado, args.out,
    )


if __name__ == "__main__":
    main()
