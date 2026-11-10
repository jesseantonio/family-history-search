"""Interface web: linha do tempo, árvore de parentesco e busca de novos sobrenomes.

O servidor só lê os arquivos que o pipeline produz em `data/processed/` e grava as
revisões em `data/annotations/review_<conjunto>.json` (o mesmo formato da antiga
interface Streamlit, já removida — revisões feitas nela continuam valendo).

    python -m src.web            # abre http://127.0.0.1:8000
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from src.graph.build import build_graph
from src.graph.validate import find_filiation_cycles, find_temporal_incompatibilities

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FHS_DATA_DIR") or ROOT / "data").resolve()   # outro diretório: testes
PROCESSED_DIR = DATA_DIR / "processed"
ANNOTATIONS_DIR = DATA_DIR / "annotations"
STATIC_DIR = Path(__file__).resolve().parent / "static"

PROGRESS_PREFIX = "@@PROGRESSO "   # mesmo prefixo de src.pipeline.PROGRESS_PREFIX
# fatia da barra geral que cada etapa ocupa: baixar os PDFs (etapa 2) é o que demora
FAIXA_ETAPA = {1: (0.00, 0.10), 2: (0.10, 0.90), 3: (0.90, 0.93), 4: (0.93, 0.97), 5: (0.97, 1.00)}

# campos enviados ao navegador: ocr_raw e text_normalized (a página inteira) ficam de fora
SLIM_FIELDS = (
    "id", "query", "bib", "periodical", "year", "edition", "physical_page", "source_url",
    "publication_date", "context_excerpt", "column_mix_suspected", "person_anchor",
    "people_mentioned", "places_mentioned", "event_type", "event_type_evidence",
    "event_type_confidence", "dates", "relations", "person_query", "search_mode", "match_text",
)
PRECISION_LABELS = {
    "dia": "dia exato",
    "mes": "mês e ano",
    "relativa": "data relativa, não resolvida",
    "publicacao": "data de publicação",
}
PRECISION_LABELS_RESOLVED = {
    "dia": "calculada pela data de publicação",
    "mes": "mês calculado pela data de publicação",
}
KINSHIP = {"filho_de", "casou_com", "viuvo_de"}
FACTS = {"residente_em": "Residência", "faleceu_em": "Falecimento"}
_DATASET_RE = re.compile(r"^[\w.\-]+_extracted\.jsonl$")
_review_lock = threading.Lock()


# --------------------------------------------------------------------------- dados
def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def resolve_timeline_date(o: dict) -> tuple[list[int], str, str | None, bool]:
    """(chave_de_ordenação, data_exibida, rótulo_da_precisão, é_data_do_evento).

    Prioriza a data do evento escrita no texto, depois a calculada a partir da data de
    publicação; sem nenhuma, usa a data (ou o ano) de publicação — marcada como tal,
    nunca disfarçada de data do evento."""
    with_year = [d for d in o.get("dates") or [] if d.get("year")]
    if with_year:
        best = min(with_year, key=lambda d: (d.get("resolved_from") is not None,
                                             {"dia": 0, "mes": 1}.get(d["precision"], 2)))
        labels = PRECISION_LABELS_RESOLVED if best.get("resolved_from") else PRECISION_LABELS
        key = [best["year"], best.get("month") or 1, best.get("day") or 1]
        return key, best["date_value"], labels.get(best["precision"], best["precision"]), True
    if o.get("publication_date"):
        return [int(x) for x in o["publication_date"].split("-")], o["publication_date"], PRECISION_LABELS["publicacao"], False
    if o.get("year"):
        return [o["year"], 1, 1], str(o["year"]), PRECISION_LABELS["publicacao"], False
    return [0, 0, 0], "sem data", None, False


def _slim(o: dict) -> dict:
    s = {k: o.get(k) for k in SLIM_FIELDS}
    s["date_key"], s["date_display"], s["date_label"], s["date_is_event"] = resolve_timeline_date(o)
    return s


_cache: dict[Path, tuple[float, list[dict], dict[str, int]]] = {}
_cache_lock = threading.Lock()


def _load(path: Path) -> tuple[list[dict], dict[str, int]]:
    """(ocorrências enxutas, posição de cada linha no arquivo), em cache até o arquivo
    mudar. A posição permite reler o registro completo (com o texto da página) sem
    manter as páginas inteiras na memória."""
    mtime = path.stat().st_mtime
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
    rows, offsets = [], {}
    with path.open("rb") as f:
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                break
            if line.strip():
                o = json.loads(line)
                offsets[o["id"]] = pos
                rows.append(_slim(o))
    with _cache_lock:
        _cache[path] = (mtime, rows, offsets)
    return rows, offsets


def load_occurrences(path: Path) -> list[dict]:
    """Ocorrências enxutas do conjunto, em cache até o arquivo mudar no disco."""
    return _load(path)[0]


def load_full_occurrence(path: Path, occ_id: str) -> dict | None:
    pos = _load(path)[1].get(occ_id)
    if pos is None:
        return None
    with path.open("rb") as f:
        f.seek(pos)
        return json.loads(f.readline())


def occurrence_context(path: Path, occ_id: str) -> dict | None:
    """Trecho ampliado de uma ocorrência: blocos vizinhos da mesma coluna no PDF em
    cache; sem o PDF, uma janela maior do texto da página."""
    from src.preprocessing.segment import _cached_pdf_path, _window_around, wide_context

    occ = load_full_occurrence(path, occ_id)
    if occ is None:
        return None
    pdf = _cached_pdf_path(occ, DATA_DIR / "raw")
    if pdf.exists():
        try:
            paragraphs = wide_context(pdf, occ["physical_page"], occ.get("match_text") or occ["query"])
            if paragraphs:
                return {"paragraphs": paragraphs, "source": "pdf"}
        except Exception:
            pass
    text = occ.get("text_normalized") or ""
    window = None
    for needle in filter(None, (occ.get("match_text"), occ["query"])):
        window = window or (_window_around(text, needle, size=1400) if text else None)
    return {"paragraphs": [window or occ.get("context_excerpt") or ""], "source": "pagina"}


def dataset_path(name: str) -> Path:
    if not _DATASET_RE.match(name):
        raise LookupError(name)
    path = PROCESSED_DIR / name
    if not path.is_file():
        raise LookupError(name)
    return path


def _dataset_label(name: str) -> tuple[str, str]:
    """occurrences_region_effting_extracted.jsonl -> ("effting", "Busca regional")."""
    stem = name.removeprefix("occurrences_").removesuffix("_extracted.jsonl")
    scope, _, surname = stem.partition("_")
    if scope == "pessoa":
        return surname, "Busca de pessoa"
    if scope == "region":
        return surname, "Busca regional"
    if scope == "indexado":
        return surname, "Índice de periódico"
    if scope.isdigit():
        return surname, f"Periódico {scope}"
    if len(scope) == 2 and scope.isalpha():
        return surname, f"Busca em {scope.upper()}"
    return stem, "Conjunto"


def list_datasets() -> list[dict]:
    out = []
    for path in sorted(PROCESSED_DIR.glob("*_extracted.jsonl")):
        if path.stat().st_size == 0:
            continue
        surname, scope = _dataset_label(path.name)
        occs = load_occurrences(path)
        if not occs:
            continue
        surname = occs[0].get("query") or surname
        person, mode = occs[0].get("person_query"), occs[0].get("search_mode")
        if person:
            scope = f"Busca de pessoa · {mode or 'inteligente'}"
        out.append({"name": path.name, "surname": surname, "scope": scope, "count": len(occs),
                    "person": person, "mode": mode,
                    "updated": path.stat().st_mtime})
    out.sort(key=lambda d: -d["updated"])
    return out


def review_path(dataset: Path) -> Path:
    return ANNOTATIONS_DIR / f"review_{dataset.stem}.json"


def load_review(dataset: Path) -> dict:
    path = review_path(dataset)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_review(dataset: Path, review: dict) -> None:
    path = review_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- locais (estados) do memoria.bn
UF_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
DEFAULT_UF = "SC"


def list_locais() -> dict:
    """Locais do campo "Local" do memoria.bn. A lista exata é guardada pelo pipeline a cada
    busca (`data/raw/memoria_locais.json`); antes da primeira busca, valem as 27 UFs."""
    from src.collector.browser_bn import LOCAIS_FILE

    path = DATA_DIR / "raw" / LOCAIS_FILE
    itens, fonte = list(UF_NAMES), "padrao"
    if path.exists():
        try:
            saved = [str(t).strip() for t in json.loads(path.read_text(encoding="utf-8")).get("locais", [])]
            if saved:
                itens, fonte = saved, "memoria.bn"
        except (ValueError, AttributeError):
            pass
    opts = [{"value": t, "label": UF_NAMES.get(t.upper(), t), "sigla": t if t.upper() in UF_NAMES else ""}
            for t in dict.fromkeys(itens)]
    opts.sort(key=lambda o: _strip_accents(o["label"]))
    default = DEFAULT_UF if any(o["value"] == DEFAULT_UF for o in opts) else (opts[0]["value"] if opts else "")
    return {"locais": opts, "padrao": default, "fonte": fonte}


# --------------------------------------------------------------------------- pessoas
def people_overrides_path(dataset: Path) -> Path:
    return ANNOTATIONS_DIR / f"people_{dataset.stem}.json"


def load_people_overrides(dataset: Path) -> dict:
    path = people_overrides_path(dataset)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"splits": list(data.get("splits", [])), "merges": [list(m) for m in data.get("merges", [])]}


def save_people_overrides(dataset: Path, data: dict) -> None:
    path = people_overrides_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


_people_cache: dict[Path, tuple[tuple, dict]] = {}


def load_people(dataset: Path) -> dict:
    """Pessoas do conjunto (ver `src.resolution.people`), refeitas quando o conjunto ou os
    ajustes manuais mudam."""
    from src.resolution.people import group_people

    ov_path = people_overrides_path(dataset)
    stamp = (dataset.stat().st_mtime, ov_path.stat().st_mtime if ov_path.exists() else None)
    hit = _people_cache.get(dataset)
    if hit and hit[0] == stamp:
        return hit[1]
    occs = load_occurrences(dataset)
    ov = load_people_overrides(dataset)
    surname = occs[0]["query"] if occs else ""
    result = group_people(occs, surname, splits=ov["splits"], merges=ov["merges"])
    _people_cache[dataset] = (stamp, result)
    return result


def find_cluster_report(surname: str) -> dict | None:
    for path in sorted(PROCESSED_DIR.glob("person_clusters_*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if str(data.get("surname", "")).lower() == surname.lower():
            return data
    return None


# --------------------------------------------------------------------------- árvore
_TITLE_RE = re.compile(r"^(?:(?:d|dr|dra|sr|sra|srta|dna|exmo|exma|snr|prof|profa|cel|cap|pe)\.?\s+)+", re.IGNORECASE)


def display_name(label: str) -> str:
    """"d. Ana Kraus" -> "Ana Kraus" (o tratamento continua visível nas evidências)."""
    cleaned = _TITLE_RE.sub("", label.strip(" ,;:")).strip()
    return cleaned or label


def _merge_people_nodes(graph, surname: str, people: dict):
    """Nós cujo nome é uma grafia de uma mesma pessoa (ver `load_people`) viram um nó só:
    "Gresorio Westrupp" e "Gregorio Westrupp" não aparecem duas vezes na árvore."""
    import networkx as nx
    from src.resolution.people import person_of_name

    by_id = {p["id"]: p for p in people["people"]}
    mapping, person_of = {}, {}
    for n, data in graph.nodes(data=True):
        pid = person_of_name(data.get("label", ""), surname, people)
        if pid:
            mapping[n] = f"pessoa:{pid}"
            person_of[mapping[n]] = pid
    if not mapping:
        return graph
    merged = nx.relabel_nodes(graph, mapping, copy=True)
    for node, pid in person_of.items():
        merged.nodes[node]["label"] = by_id[pid]["name"]
        merged.nodes[node]["person"] = pid
    merged.remove_edges_from([(u, v, k) for u, v, k in merged.edges(keys=True) if u == v])
    return merged


def build_tree(occurrences: list[dict], review: dict, surname: str, people: dict | None = None) -> dict:
    """Grafo da Fase 6 convertido para a árvore da interface: pessoas e relações de
    parentesco (filiação e casamento). Residência e falecimento viram fatos da pessoa,
    não nós — o alvo deles é um lugar ou uma data."""
    graph = build_graph(occurrences, find_cluster_report(surname), review)
    if people:
        graph = _merge_people_nodes(graph, surname, people)
    cycles = find_filiation_cycles(graph)
    flags = find_temporal_incompatibilities(graph)
    flagged_pairs = {(f["filho"], f["pai_ou_mae"]) for f in flags}
    cycle_nodes = {n for c in cycles for n in c}
    needle = _strip_accents(surname)

    kin_nodes: set[str] = set()
    for u, v, d in graph.edges(data=True):
        if d["relation"] in KINSHIP:
            kin_nodes.update((u, v))

    def edge_json(u, v, d):
        return {
            "source": u, "target": v, "relation": d["relation"], "status": d.get("status", "extraida"),
            "confidence": d.get("confidence"), "evidence": d.get("evidence"), "source_url": d.get("source_url"),
            "occurrence_id": d.get("occurrence_id"), "relation_index": d.get("relation_index"),
            "periodical": d.get("periodical"), "year": d.get("year"),
            "flagged": (u, v) in flagged_pairs or (u in cycle_nodes and v in cycle_nodes and d["relation"] == "filho_de"),
        }

    nodes = {}
    for n in kin_nodes:
        label = display_name(graph.nodes[n].get("label", n))
        nodes[n] = {"id": n, "label": label, "bears_surname": needle in _strip_accents(label),
                    "flagged": n in cycle_nodes, "facts": [], "mentions": set(),
                    "person": graph.nodes[n].get("person")}
    edges = []
    for u, v, d in graph.edges(data=True):
        if d["relation"] in KINSHIP:
            edges.append(edge_json(u, v, d))
            for n in (u, v):
                nodes[n]["mentions"].add(d.get("occurrence_id"))
        elif d["relation"] in FACTS and u in nodes:
            nodes[u]["facts"].append({**edge_json(u, v, d), "label": FACTS[d["relation"]],
                                      "value": graph.nodes[v].get("label", v)})
    for n in nodes.values():
        n["mentions"] = len(n["mentions"])
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "cycles": [[display_name(graph.nodes[n].get("label", n)) for n in c] for c in cycles],
        "flags": [{**f, "filho": display_name(graph.nodes[f["filho"]].get("label", f["filho"])),
                   "pai_ou_mae": display_name(graph.nodes[f["pai_ou_mae"]].get("label", f["pai_ou_mae"]))} for f in flags],
    }


# --------------------------------------------------------------------------- busca (pipeline em segundo plano)
_search: dict = {}
_search_lock = threading.Lock()


def start_search(surname: str, uf: str, ano_inicio: int | None, ano_fim: int | None, *,
                 nome: str | None = None, modo: str = "inteligente") -> None:
    """Família (só `surname`) ou pessoa (`nome` + `surname`, modo literal ou inteligente)."""
    from src.pipeline import output_base

    with _search_lock:
        if _search.get("proc") is not None and _search["proc"].poll() is None:
            raise RuntimeError("já existe uma busca em andamento")
        cmd = [sys.executable, "-m", "src.pipeline", "--surname", surname, "--uf", uf, "--data-dir", str(DATA_DIR)]
        if nome:
            cmd += ["--nome", nome, "--modo", modo]
        if ano_inicio is not None:
            cmd += ["--ano-inicio", str(ano_inicio)]
        if ano_fim is not None:
            cmd += ["--ano-fim", str(ano_fim)]
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        base = output_base(surname, nome)
        log_path = PROCESSED_DIR / f"busca_{base.removeprefix('region_')}.log"
        if _search.get("log_file"):
            _search["log_file"].close()
        log_file = log_path.open("w", encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        except Exception:
            log_file.close()
            raise
        _search.clear()
        _search.update(proc=proc, log_path=log_path, log_file=log_file, surname=surname, started=time.time(),
                       who=f"{nome} {surname}" if nome else surname, kind="pessoa" if nome else "familia",
                       dataset=f"occurrences_{base}_extracted.jsonl")


def cancel_search() -> bool:
    """Interrompe a busca em andamento (ex.: travada esperando o site). O Chrome continua
    aberto; os PDFs já baixados ficam no cache e a etapa 2 retoma de onde parou."""
    with _search_lock:
        proc = _search.get("proc")
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        _search["cancelled"] = True
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


def _fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, round(seconds))} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def search_status() -> dict:
    with _search_lock:
        if not _search:
            return {"state": "idle"}
        proc, log_path, surname = _search["proc"], _search["log_path"], _search["surname"]
        rc = proc.poll()
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lines, progress = [], None
    stage, stage_t0, stage_done0 = 0, None, 0
    for line in text.splitlines():
        if line.startswith(PROGRESS_PREFIX):
            try:
                p = json.loads(line[len(PROGRESS_PREFIX):])
            except ValueError:
                continue
            if p["etapa"] != stage:
                stage, stage_t0, stage_done0 = p["etapa"], p.get("t"), p["feitos"]
            progress = p
        elif line.strip():
            lines.append(line.rstrip())
    fraction, label, eta = 0.0, "Abrindo o Chrome", None
    if progress:
        lo, hi = FAIXA_ETAPA.get(progress["etapa"], (0, 1))
        done, total = progress["feitos"], progress["total"]
        fraction = lo + (hi - lo) * (done / total if total else 0)
        label = f"Etapa {progress['etapa']} de 5 · {progress['nome']}" + (f" · {done} de {total}" if total else "")
        moved = done - stage_done0
        if moved > 0 and done < total and stage_t0 and progress.get("t"):
            eta = _fmt_duration((progress["t"] - stage_t0) / moved * (total - done))
    state = "running" if rc is None else ("done" if rc == 0 else
                                          "cancelled" if _search.get("cancelled") else "failed")
    if rc is not None:
        fraction = 1.0 if rc == 0 else fraction
        with _search_lock:
            if _search.get("log_file"):
                _search["log_file"].close()
                _search["log_file"] = None
    return {
        "state": state, "surname": surname, "fraction": round(min(1.0, fraction), 4), "label": label, "eta": eta,
        "log": lines[-14:], "log_path": str(log_path),
        "who": _search.get("who") or surname, "kind": _search.get("kind", "familia"),
        "dataset": _search.get("dataset") or f"occurrences_region_{surname.lower()}_extracted.jsonl",
    }


# --------------------------------------------------------------------------- rotas
def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def index(request: Request):
    return FileResponse(STATIC_DIR / "index.html")


async def api_datasets(request: Request):
    return JSONResponse(list_datasets())


def _dataset_or_404(request: Request) -> Path:
    return dataset_path(request.path_params["name"])


async def api_occurrences(request: Request):
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    return JSONResponse({"occurrences": load_occurrences(path), "review": load_review(path)})


async def api_tree(request: Request):
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    occs = load_occurrences(path)
    surname = occs[0]["query"] if occs else ""
    return JSONResponse(build_tree(occs, load_review(path), surname, load_people(path)))


async def api_people(request: Request):
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    return JSONResponse(load_people(path))


async def api_people_adjust(request: Request):
    """Ajuste manual do agrupamento. Corpo:
    {"action": "split", "key": k}       grafia k vira uma pessoa à parte
    {"action": "merge", "a": k1, "b": k2}   as pessoas das grafias k1 e k2 viram uma só
    {"action": "reset", "key": k}       desfaz ajustes que envolvem a grafia k"""
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    body = await request.json()
    action = body.get("action")
    ov = load_people_overrides(path)
    if action == "split" and body.get("key"):
        key = body["key"]
        if key not in ov["splits"]:
            ov["splits"].append(key)
        ov["merges"] = [m for m in ov["merges"] if key not in m]
    elif action == "merge" and body.get("a") and body.get("b") and body["a"] != body["b"]:
        pair = [body["a"], body["b"]]
        if pair not in ov["merges"] and pair[::-1] not in ov["merges"]:
            ov["merges"].append(pair)
    elif action == "reset" and body.get("key"):
        key = body["key"]
        ov["splits"] = [k for k in ov["splits"] if k != key]
        ov["merges"] = [m for m in ov["merges"] if key not in m]
    else:
        return _error("ajuste inválido")
    save_people_overrides(path, ov)
    return JSONResponse(load_people(path))


async def api_context(request: Request):
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    ctx = occurrence_context(path, request.path_params["occ"])
    if ctx is None:
        return _error("ocorrência não encontrada", 404)
    return JSONResponse(ctx)


async def api_review(request: Request):
    """Corpo: {"occurrence_id", "status"} para a ocorrência, ou
    {"occurrence_id", "relation_index", "status"} para uma relação."""
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    body = await request.json()
    occ_id, status = body.get("occurrence_id"), body.get("status")
    rel = body.get("relation_index")
    if not occ_id or not isinstance(status, str):
        return _error("informe occurrence_id e status")
    allowed = {"extraida", "confirmada", "rejeitada"} if rel is not None else {"pendente", "aprovado", "rejeitado"}
    if status not in allowed:
        return _error(f"status inválido: {status}")
    with _review_lock:
        review = load_review(path)
        entry = review.setdefault(occ_id, {})
        if rel is None:
            entry["status"] = status
        else:
            entry.setdefault("relations", {})[str(int(rel))] = status
        save_review(path, review)
    return JSONResponse({"ok": True, "review": review})


EXPORT_FIELDS = ["id", "query", "bib", "periodical", "year", "edition", "physical_page", "date_display",
                 "event_type", "context_excerpt", "source_url"]


async def api_export(request: Request):
    try:
        path = _dataset_or_404(request)
    except LookupError:
        return _error("conjunto não encontrado", 404)
    fmt = request.path_params["fmt"]
    if fmt not in ("json", "csv"):
        return _error("formato inválido", 404)
    review = load_review(path)
    approved = [o for o in load_occurrences(path) if review.get(o["id"], {}).get("status") == "aprovado"]
    base = path.stem.removesuffix("_extracted")
    if fmt == "json":
        body = json.dumps(approved, ensure_ascii=False, indent=2)
        media = "application/json"
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        for o in approved:
            writer.writerow({k: o.get(k) for k in EXPORT_FIELDS})
        body, media = "﻿" + buf.getvalue(), "text/csv"   # BOM: o Excel abre com acentos certos
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{base}_aprovados.{fmt}"'})


async def api_search_get(request: Request):
    return JSONResponse(search_status())


async def api_search_post(request: Request):
    body = await request.json()
    surname = str(body.get("surname") or "").strip()
    uf = str(body.get("uf") or DEFAULT_UF).strip()
    if not re.fullmatch(r"[^\W\d_][\w' \-]{1,60}", surname):
        return _error("sobrenome inválido")
    allowed = {o["value"] for o in list_locais()["locais"]}
    if uf not in allowed and uf.upper() in allowed:
        uf = uf.upper()
    if uf not in allowed:
        return _error("estado inválido")
    kind = body.get("kind") or "familia"
    nome, modo = None, "inteligente"
    if kind == "pessoa":
        nome = str(body.get("nome") or "").strip()
        modo = str(body.get("modo") or "inteligente")
        if not re.fullmatch(r"[^\W\d_][\w' .\-]{0,60}", nome):
            return _error("informe o nome da pessoa")
        if modo not in ("literal", "inteligente"):
            return _error("modo inválido")
    elif kind != "familia":
        return _error("tipo de busca inválido")

    def year(key):
        v = body.get(key)
        if v in (None, ""):
            return None
        v = int(v)
        if not 1500 <= v <= 2100:
            raise ValueError(key)
        return v

    try:
        ano_inicio, ano_fim = year("ano_inicio"), year("ano_fim")
    except (TypeError, ValueError):
        return _error("ano inválido")
    if ano_inicio and ano_fim and ano_inicio > ano_fim:
        return _error("o ano inicial precisa ser menor ou igual ao final")
    try:
        start_search(surname, uf, ano_inicio, ano_fim, nome=nome, modo=modo)
    except RuntimeError as exc:
        return _error(str(exc), 409)
    return JSONResponse(search_status())


async def api_locais(request: Request):
    return JSONResponse(list_locais())


async def api_search_cancel(request: Request):
    if not cancel_search():
        return _error("não há busca em andamento", 409)
    return JSONResponse(search_status())


routes = [
    Route("/", index),
    Route("/api/datasets", api_datasets),
    Route("/api/datasets/{name}/occurrences", api_occurrences),
    Route("/api/datasets/{name}/tree", api_tree),
    Route("/api/datasets/{name}/people", api_people, methods=["GET"]),
    Route("/api/datasets/{name}/people", api_people_adjust, methods=["POST"]),
    Route("/api/datasets/{name}/occurrences/{occ}/context", api_context),
    Route("/api/datasets/{name}/review", api_review, methods=["POST"]),
    Route("/api/datasets/{name}/export.{fmt:str}", api_export),
    Route("/api/search", api_search_get, methods=["GET"]),
    Route("/api/search", api_search_post, methods=["POST"]),
    Route("/api/search/cancel", api_search_cancel, methods=["POST"]),
    Route("/api/locais", api_locais),
    Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
]

app = Starlette(routes=routes)
