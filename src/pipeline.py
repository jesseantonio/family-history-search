"""Sobrenome → memoria.bn (listagem) → PDFs da Hemeroteca (trechos) → timeline.

    python -m src.pipeline --surname Sautner --uf SC

Etapas, na ordem:
1. listar   : memoria.bn via Chrome real (sem abrir o DocReader, que pede CAPTCHA)
              → data/processed/listagem_<s>.csv
2. coletar  : baixa os PDFs listados e acha as páginas com o sobrenome
              → data/processed/occurrences_region_<s>.jsonl
3. segment  : recorta o trecho ao redor do nome → ..._enriched.jsonl
4. extraction: pessoas, datas, eventos, relações → ..._extracted.jsonl
5. resolution: agrupa menções da mesma pessoa → person_clusters_region_<s>.json
Depois: python -m src.web (linha do tempo, árvore e pessoas no navegador)

Busca de uma pessoa (em vez da família inteira):

    python -m src.pipeline --surname Barletta --nome Carlos --modo inteligente

grava em occurrences_pessoa_carlos-barletta_*.jsonl. Ver `run`.

Use `--so-listar` para parar depois da etapa 1 (revisar o CSV) e
`--da-listagem` para retomar a partir de um CSV já salvo.

Retomada: durante a etapa 2, cada edição concluída é anotada em
`coleta_<s>.jsonl`. Se a execução for interrompida, a próxima pula as
edições já concluídas e mantém as ocorrências já gravadas (as que falharam
são tentadas de novo). O arquivo é apagado quando a etapa 2 termina. Use
`--recomecar` para ignorá-lo e refazer tudo.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

from src.collector import browser_bn
from src.collector.editions import STATUS_CONCLUIDOS, EditionHit, filtrar_periodo, load_listagem, save_listagem
from src.collector.pdf_collect import DEFAULT_WORKERS, coletar

logger = logging.getLogger("pipeline")

# Linha que a tela (Streamlit) lê pra desenhar a barra de progresso.
PROGRESS_PREFIX = "@@PROGRESSO "
ETAPAS = {1: "Pesquisando no memoria.bn", 2: "Baixando PDFs e procurando o nome",
          3: "Recortando trechos", 4: "Extraindo pessoas, datas e relações", 5: "Agrupando menções"}


def report(etapa: int, feitos: int = 0, total: int = 0) -> None:
    print(PROGRESS_PREFIX + json.dumps({"etapa": etapa, "nome": ETAPAS[etapa], "feitos": feitos,
                                        "total": total, "t": round(time.time(), 1)}), flush=True)


def _carregar_checkpoint(path: Path, editions: list[EditionHit]) -> set[tuple]:
    """Lê `coleta_<s>.jsonl` e devolve as chaves das edições já concluídas, restaurando
    nelas o status e as páginas encontradas."""
    if not path.exists():
        return set()
    feitas: dict[tuple, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:          # última linha cortada pela interrupção
                continue
            if r.get("status") in STATUS_CONCLUIDOS:
                feitas[(r["bib"], r["ano"], r["edicao"])] = r
    for ed in editions:
        r = feitas.get(ed.key)
        if r:
            ed.status, ed.paginas_pdf = r["status"], list(r.get("paginas_pdf") or [])
    return set(feitas) & {ed.key for ed in editions}


def _ocorrencias_ja_gravadas(occ_path: Path, chaves: set[tuple]) -> list[str]:
    """Linhas do JSONL de ocorrências que pertencem a edições já concluídas (sem repetir id)."""
    if not occ_path.exists() or not chaves:
        return []
    linhas, vistos = [], set()
    with occ_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if (o["bib"], o["year"], o["edition"]) in chaves and o["id"] not in vistos:
                vistos.add(o["id"])
                linhas.append(line if line.endswith("\n") else line + "\n")
    return linhas


MODOS = ("inteligente", "literal")


def person_slug(nome: str, surname: str) -> str:
    """"Carlos", "Barletta" -> "carlos-barletta" (nome de arquivo sem acentos nem espaços)."""
    from src.resolution.people import fold
    return re.sub(r"[^a-z0-9]+", "-", fold(f"{nome} {surname}")).strip("-")


def output_base(surname: str, nome: str | None = None) -> str:
    """Prefixo dos arquivos de uma busca: "region_barletta" (família) ou
    "pessoa_carlos-barletta" (pessoa). O conjunto final é occurrences_<prefixo>_extracted.jsonl."""
    return f"pessoa_{person_slug(nome, surname)}" if nome else f"region_{surname.lower()}"


def _listar_varios(page, termos: list[str], uf: str, data_dir: Path, ano_inicio, ano_fim) -> list[EditionHit]:
    """Pesquisa cada grafia do sobrenome no memoria.bn e junta as edições (sem repetir)."""
    editions: dict[tuple, EditionHit] = {}
    for k, termo in enumerate(termos, start=1):
        if len(termos) > 1:
            logger.info("grafia %d de %d: %r", k, len(termos), termo)
        for ed in browser_bn.listar(page, termo, uf, ano_inicio=ano_inicio, ano_fim=ano_fim,
                                    debug_dir=data_dir / "raw", progress=lambda i, n: report(1, i, n),
                                    locais_path=data_dir / "raw" / browser_bn.LOCAIS_FILE):
            editions.setdefault(ed.key, ed)
    return list(editions.values())


def run(surname: str, uf: str, data_dir: Path, *, ano_inicio: int | None = None,
        ano_fim: int | None = None, so_listar: bool = False,
        da_listagem: bool = False, recomecar: bool = False, delay: float = 1.0,
        workers: int = DEFAULT_WORKERS, nome: str | None = None, modo: str = "inteligente") -> None:
    """Busca de família (só `surname`) ou de uma pessoa (`nome` + `surname`).

    Na busca de pessoa, o memoria.bn é pesquisado pelo sobrenome (no modo inteligente, também
    pelas grafias prováveis, ex. "Barleta") e cada página baixada é filtrada pelo nome: no
    modo literal, como digitado; no inteligente, aceitando erros de OCR e nomes do meio."""
    if ano_inicio is not None and ano_fim is not None and ano_inicio > ano_fim:
        raise SystemExit(f"ano inicial ({ano_inicio}) maior que o final ({ano_fim})")
    if modo not in MODOS:
        raise SystemExit(f"modo inválido: {modo!r} (use {' ou '.join(MODOS)})")
    nome = (nome or "").strip() or None
    base = output_base(surname, nome)
    slug = base.removeprefix("region_")
    processed = data_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    listagem = processed / f"listagem_{slug}.csv"
    occ_path = processed / f"occurrences_{base}.jsonl"
    enriched = processed / f"occurrences_{base}_enriched.jsonl"
    extracted = processed / f"occurrences_{base}_extracted.jsonl"
    clusters = processed / f"person_clusters_{base}.json"
    checkpoint = processed / f"coleta_{slug}.jsonl"

    person = None
    alvo = surname
    termos = [surname]
    if nome:
        from src.resolution.people import find_person, surname_variants

        smart = modo == "inteligente"
        alvo = f"{nome} {surname}"
        termos = surname_variants(surname) if smart else [surname]

        def match(text: str) -> str | None:
            found = find_person(text, nome, surname, smart=smart)
            return found[0].raw if found else None

        person = {"query": alvo, "mode": modo, "match": match}

    periodo = f"{ano_inicio or 'início'}–{ano_fim or 'hoje'}"
    with browser_bn.BrowserSession(data_dir) as (context, page):
        report(1)
        if da_listagem:
            editions = load_listagem(listagem)
            logger.info("[1/5] listagem lida de %s (%d edições)", listagem, len(editions))
        else:
            if nome:
                logger.info("[1/5] busca de pessoa: %r (modo %s); pesquisando no memoria.bn %s "
                            "(UF=%s, período %s)…", alvo, modo, " e ".join(repr(t) for t in termos), uf, periodo)
            else:
                logger.info("[1/5] pesquisando %r no memoria.bn (UF=%s, período %s)…", surname, uf, periodo)
            editions = _listar_varios(page, termos, uf, data_dir, ano_inicio, ano_fim)
        total_listado = len(editions)
        editions = filtrar_periodo(editions, ano_inicio, ano_fim)
        if len(editions) != total_listado:
            logger.info("período %s: %d de %d edição(ões) mantidas", periodo, len(editions), total_listado)
        if not da_listagem:
            save_listagem(editions, surname, listagem)
            logger.info("listagem salva em %s", listagem)
        if not editions:
            logger.error("Nenhuma edição listada para %r em %s no período %s. Confira no site "
                         "(memoria.bn.gov.br, aba Local) se a busca traz resultados.", surname, uf, periodo)
            raise SystemExit(2)
        if so_listar:
            return

        logger.info("[2/5] baixando PDFs e localizando o sobrenome…")
        if recomecar:
            checkpoint.unlink(missing_ok=True)
        ja_feitas = _carregar_checkpoint(checkpoint, editions)
        mantidas = _ocorrencias_ja_gravadas(occ_path, ja_feitas)
        if ja_feitas:
            logger.info("retomando: %d edição(ões) já concluídas, %d ocorrência(s) mantidas",
                        len(ja_feitas), len(mantidas))
        n = len(mantidas)
        with occ_path.open("w", encoding="utf-8") as f, checkpoint.open("a", encoding="utf-8") as ck:
            f.writelines(mantidas)
            f.flush()

            def ao_concluir(ed: EditionHit) -> None:
                f.flush()   # as ocorrências da edição precisam estar no disco antes do checkpoint
                ck.write(json.dumps({"bib": ed.bib, "ano": ed.ano, "edicao": ed.edicao, "status": ed.status,
                                     "paginas_pdf": ed.paginas_pdf}, ensure_ascii=False) + "\n")
                ck.flush()

            for occ in coletar(editions, surname, data_dir / "raw", browser_context=context, delay=delay,
                               workers=workers,
                               progress=lambda i, total: report(2, i, total), ja_feitas=ja_feitas,
                               ao_concluir=ao_concluir, person=person):
                f.write(json.dumps(occ.to_dict(), ensure_ascii=False) + "\n")
                n += 1
        checkpoint.unlink(missing_ok=True)
        save_listagem(editions, surname, listagem)   # grava status por edição
        logger.info("%d página(s) com %s", n, f"o nome {alvo!r}" if nome else "o sobrenome")

    from src.preprocessing import segment
    from src.extraction import pipeline as extraction
    from src.resolution import pipeline as resolution

    logger.info("[3/5] recortando trechos…")
    report(3)
    segment.main(["--in", str(occ_path), "--out", str(enriched), "--data-dir", str(data_dir)])
    logger.info("[4/5] extraindo pessoas, datas e relações…")
    report(4)
    extraction.main(["--in", str(enriched), "--out", str(extracted)])
    logger.info("[5/5] agrupando menções…")
    report(5)
    resolution.main(["--in", str(extracted), "--surname", surname, "--out", str(clusters)])
    report(5, 1, 1)

    faltando = [e for e in editions if e.status != "ok"]
    if faltando:
        logger.warning("%d edição(ões) sem trecho — veja a coluna 'status' de %s", len(faltando), listagem)
    print(f"\nPronto. Abra a interface com:\n  python -m src.web\n"
          f"e escolha '{extracted.name}' no seletor \"Busca\", no topo.")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Busca um sobrenome na Hemeroteca Digital de ponta a ponta.")
    p.add_argument("--surname", required=True)
    p.add_argument("--uf", default="SC", help="Local no memoria.bn (ex.: SC, PR, RS)")
    p.add_argument("--ano-inicio", type=int, default=None, help="só edições a partir deste ano")
    p.add_argument("--ano-fim", type=int, default=None, help="só edições até este ano")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--delay", type=float, default=1.0,
                   help="pausa (s) depois de cada PDF baixado, em cada download simultâneo")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"PDFs baixados ao mesmo tempo (padrão {DEFAULT_WORKERS}; 1 = um por vez)")
    p.add_argument("--so-listar", action="store_true", help="para depois de gerar listagem_<s>.csv")
    p.add_argument("--da-listagem", action="store_true", help="pula a busca e usa listagem_<s>.csv existente")
    p.add_argument("--recomecar", action="store_true",
                   help="ignora o ponto de retomada (coleta_<s>.jsonl) e refaz a etapa 2 inteira")
    p.add_argument("--nome", default=None,
                   help="prenome(s) da pessoa: busca só ela, não a família inteira (ex.: --nome Carlos)")
    p.add_argument("--modo", choices=MODOS, default="inteligente",
                   help="busca de pessoa: 'literal' (nome como digitado) ou 'inteligente' "
                        "(aceita erros de OCR e grafias, ex. Carlo Barleta; padrão)")
    a = p.parse_args(argv)
    run(a.surname, a.uf, a.data_dir, ano_inicio=a.ano_inicio, ano_fim=a.ano_fim,
        so_listar=a.so_listar, da_listagem=a.da_listagem, recomecar=a.recomecar, delay=a.delay,
        workers=a.workers, nome=a.nome, modo=a.modo)


if __name__ == "__main__":
    main()
