"""Fase 4 (timeline) + Fase 6 (grafo de relações) — Streamlit.

Timeline: lê o resultado da Fase 3 (`occurrences_*_extracted.jsonl`),
ordena por data (usando a data do EVENTO extraída do texto quando existir;
cai pra data de publicação da edição, claramente rotulada como tal,
quando não existir — nunca inventa dia ou mês), permite filtrar por
pessoa, lugar, periódico e tipo de evento, e permite aprovar/rejeitar cada
ocorrência e cada relação extraída.

Grafo: nós são pessoas candidatas (menções do sobrenome resolvidas pra um
cluster da Fase 5 quando possível), arestas são as relações extraídas na
Fase 3 — tracejado laranja quando ainda é hipótese ("extraida"), verde
sólido quando confirmada na aba de timeline. Vermelho sinaliza ciclo de
filiação (logicamente impossível) ou incompatibilidade temporal (sinal
fraco, baseado em ano de ocorrência — não prova nada, só levanta suspeita
pra revisão). Nenhuma aresta aparece sem evidência e link pra fonte.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import streamlit as st

from src.graph import build_graph, find_filiation_cycles, find_temporal_incompatibilities, render_graph_html

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PROCESSED_DIR = DATA_DIR / "processed"
ANNOTATIONS_DIR = DATA_DIR / "annotations"

PRECISION_LABELS = {
    "dia": "dia exato",
    "mes": "mês/ano",
    "relativa": "data relativa (não resolvida)",
    "publicacao": "só data de publicação — evento não tem data própria no texto",
}

EXPORT_FIELDS = [
    "id", "query", "bib", "periodical", "year", "edition", "physical_page",
    "event_type", "context_excerpt", "source_url",
]


def list_datasets() -> list[Path]:
    return sorted(PROCESSED_DIR.glob("*_extracted.jsonl"))


@st.cache_data
def load_occurrences(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def resolve_timeline_date(occurrence: dict) -> tuple[tuple[int, int, int], str, str | None]:
    """Retorna (chave_de_ordenação, texto_pra_exibir, precisão).

    Prioriza a data do evento extraída do texto (a mais específica, se
    houver mais de uma); cai pra ano de publicação — marcado como tal,
    nunca disfarçado de data do evento — quando nenhuma data foi extraída.
    """
    dates = occurrence.get("dates") or []
    with_year = [d for d in dates if d.get("year")]
    if with_year:
        best = min(with_year, key=lambda d: {"dia": 0, "mes": 1}.get(d["precision"], 2))
        year, month, day = best["year"], best.get("month") or 1, best.get("day") or 1
        return (year, month, day), best["date_value"], best["precision"]
    year = occurrence.get("year")
    if year:
        return (year, 1, 1), str(year), "publicacao"
    return (0, 0, 0), "sem data", None


def review_path_for(dataset_path: Path) -> Path:
    return ANNOTATIONS_DIR / f"review_{dataset_path.stem}.json"


def load_review(dataset_path: Path) -> dict:
    path = review_path_for(dataset_path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_review(dataset_path: Path, review: dict) -> None:
    path = review_path_for(dataset_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")


def _matches_name_filter(occurrence: dict, needle: str) -> bool:
    names = [(occurrence.get("person_anchor") or {}).get("name_raw", "")]
    names += [p["name_raw"] for p in occurrence.get("people_mentioned", [])]
    return any(needle.lower() in (n or "").lower() for n in names)


def _matches_place_filter(occurrence: dict, needle: str) -> bool:
    places = [p["name_raw"] for p in occurrence.get("places_mentioned", [])]
    return any(needle.lower() in (n or "").lower() for n in places)


@st.cache_data
def find_cluster_report(surname: str) -> dict | None:
    """Procura um relatório de clusters da Fase 5 (`person_clusters_*.json`)
    cujo campo "surname" bata com o sobrenome do conjunto de dados. Sem
    isso, o grafo ainda funciona — só não deduplica menções em clusters,
    cada nome normalizado diferente vira um nó próprio.
    """
    for path in PROCESSED_DIR.glob("person_clusters_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("surname", "").lower() == surname.lower():
            return data
    return None


def _export_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in EXPORT_FIELDS})
    return buf.getvalue()


def main() -> None:
    st.set_page_config(page_title="Linha do tempo — Family History Search", layout="wide")
    st.title("Linha do tempo")

    datasets = list_datasets()
    if not datasets:
        st.warning(f"Nenhum arquivo *_extracted.jsonl encontrado em {PROCESSED_DIR}")
        return

    dataset_path = st.sidebar.selectbox("Conjunto de dados", datasets, format_func=lambda p: p.name)
    occurrences = load_occurrences(str(dataset_path))

    if st.session_state.get("review_dataset") != str(dataset_path):
        st.session_state["review"] = load_review(dataset_path)
        st.session_state["review_dataset"] = str(dataset_path)
    review: dict = st.session_state["review"]

    st.sidebar.header("Filtros")
    periodicos = sorted({o["periodical"] for o in occurrences if o.get("periodical")})
    periodico_sel = st.sidebar.multiselect("Periódico", periodicos)

    tipos = sorted({o.get("event_type", "outro") for o in occurrences})
    tipo_sel = st.sidebar.multiselect("Tipo de evento", tipos)

    pessoa_busca = st.sidebar.text_input("Pessoa (busca por nome)")
    lugar_busca = st.sidebar.text_input("Lugar (busca por nome)")
    apenas_pendentes = st.sidebar.checkbox("Mostrar só pendentes de revisão")
    ordem = st.sidebar.radio("Ordenar", ["mais antigo primeiro", "mais recente primeiro"])

    filtered = []
    for o in occurrences:
        if periodico_sel and o.get("periodical") not in periodico_sel:
            continue
        if tipo_sel and o.get("event_type", "outro") not in tipo_sel:
            continue
        if pessoa_busca and not _matches_name_filter(o, pessoa_busca):
            continue
        if lugar_busca and not _matches_place_filter(o, lugar_busca):
            continue
        status = review.get(o["id"], {}).get("status", "pendente")
        if apenas_pendentes and status != "pendente":
            continue
        filtered.append(o)

    enriched = [(*resolve_timeline_date(o), o) for o in filtered]
    enriched.sort(key=lambda t: t[0], reverse=(ordem == "mais recente primeiro"))

    tab_timeline, tab_graph = st.tabs(["📅 Linha do tempo", "🕸️ Grafo de relações"])

    with tab_timeline:
        st.write(f"**{len(enriched)}** ocorrência(s) exibidas — {len(occurrences)} no total no conjunto")

        aprovados = [o for o in occurrences if review.get(o["id"], {}).get("status") == "aprovado"]
        ecol1, ecol2 = st.sidebar.columns(2)
        with ecol1:
            st.download_button(
                "Exportar aprovados (JSON)", data=json.dumps(aprovados, ensure_ascii=False, indent=2),
                file_name="aprovados.json", mime="application/json", disabled=not aprovados,
            )
        with ecol2:
            st.download_button(
                "Exportar aprovados (CSV)", data=_export_csv(aprovados),
                file_name="aprovados.csv", mime="text/csv", disabled=not aprovados,
            )
        if not aprovados:
            st.sidebar.caption("Nenhuma ocorrência aprovada ainda — exportação fica disponível depois de aprovar pelo menos uma.")

        for _sort_key, display, precision, o in enriched:
            status = review.get(o["id"], {}).get("status", "pendente")
            label_precisao = PRECISION_LABELS.get(precision, precision or "sem data")
            header = f"{display} — {label_precisao} — {o.get('periodical', '?')} — {o.get('event_type', 'outro')} — [{status}]"

            with st.expander(header):
                col_main, col_actions = st.columns([3, 1])

                with col_main:
                    st.markdown(f"**Trecho:**\n\n> {o['context_excerpt']}")

                    if o.get("event_type_evidence"):
                        st.caption(
                            f"Classificado como \"{o['event_type']}\" pela palavra \"{o['event_type_evidence']}\" "
                            f"(confiança {o['event_type_confidence']:.0%})"
                        )

                    anchor = o.get("person_anchor")
                    if anchor:
                        st.write(f"**Pessoa (sobrenome buscado):** {anchor['name_raw']}")
                    if o.get("people_mentioned"):
                        st.write("**Outras pessoas mencionadas:** " + ", ".join(p["name_raw"] for p in o["people_mentioned"]))
                    if o.get("places_mentioned"):
                        st.write("**Lugares mencionados:** " + ", ".join(p["name_raw"] for p in o["places_mentioned"]))

                    if o.get("relations"):
                        st.write("**Relações extraídas** (evidência entre parênteses):")
                        rel_review = review.get(o["id"], {}).get("relations", {})
                        for i, rel in enumerate(o["relations"]):
                            rel_status = rel_review.get(str(i), rel.get("status", "extraida"))
                            rcol_text, rcol_confirm, rcol_reject = st.columns([5, 1, 1])
                            with rcol_text:
                                st.caption(
                                    f"`{rel['relation']}`  {rel.get('subject_raw') or '?'} → {rel['target_raw']}  "
                                    f"(confiança {rel['confidence']:.0%}, status: {rel_status})  \n"
                                    f"_\"{rel['evidence']}\"_"
                                )
                            with rcol_confirm:
                                if st.button("Confirmar", key=f"confirm_{o['id']}_{i}"):
                                    review.setdefault(o["id"], {}).setdefault("relations", {})[str(i)] = "confirmada"
                                    save_review(dataset_path, review)
                                    st.rerun()
                            with rcol_reject:
                                if st.button("Rejeitar", key=f"reject_{o['id']}_{i}"):
                                    review.setdefault(o["id"], {}).setdefault("relations", {})[str(i)] = "rejeitada"
                                    save_review(dataset_path, review)
                                    st.rerun()

                    st.markdown(
                        f"[Ver fonte original]({o['source_url']}) — edição {o['edition']}, página {o['physical_page']}"
                    )

                with col_actions:
                    st.write(f"Status: **{status}**")
                    if st.button("✅ Aprovar", key=f"approve_{o['id']}"):
                        review.setdefault(o["id"], {})["status"] = "aprovado"
                        save_review(dataset_path, review)
                        st.rerun()
                    if st.button("❌ Rejeitar", key=f"reject_occ_{o['id']}"):
                        review.setdefault(o["id"], {})["status"] = "rejeitado"
                        save_review(dataset_path, review)
                        st.rerun()
                    if status != "pendente" and st.button("↺ Voltar p/ pendente", key=f"reset_{o['id']}"):
                        review.setdefault(o["id"], {})["status"] = "pendente"
                        save_review(dataset_path, review)
                        st.rerun()

    with tab_graph:
        surname = occurrences[0]["query"] if occurrences else ""
        cluster_report = find_cluster_report(surname)
        if cluster_report is None:
            st.caption(
                "Não achei um `person_clusters_*.json` (Fase 5) pra este sobrenome — o grafo "
                "ainda funciona, mas cada grafia de nome diferente vira um nó separado, sem "
                "deduplicar pela mesma pessoa candidata."
            )

        graph = build_graph(occurrences, cluster_report, review)
        cycles = find_filiation_cycles(graph)
        temporal_flags = find_temporal_incompatibilities(graph)

        gcol1, gcol2, gcol3 = st.columns(3)
        gcol1.metric("Nós (pessoas candidatas)", graph.number_of_nodes())
        gcol2.metric("Arestas (relações)", graph.number_of_edges())
        gcol3.metric("Sinalizações", len(cycles) + len(temporal_flags))

        st.caption(
            "🟧 tracejado = relação extraída (hipótese, ainda não revisada)　"
            "🟩 sólido = confirmada na aba de timeline　"
            "🟥 = ciclo de filiação ou incompatibilidade temporal — ver detalhes abaixo"
        )

        if cycles:
            with st.expander(f"⚠️ {len(cycles)} ciclo(s) de filiação — logicamente impossíveis", expanded=True):
                for cycle in cycles:
                    st.write(" → ".join(cycle) + f" → {cycle[0]}")

        if temporal_flags:
            with st.expander(f"⚠️ {len(temporal_flags)} incompatibilidade(s) temporal(is) — sinal fraco"):
                for flag in temporal_flags:
                    st.write(f"{flag['filho']} → {flag['pai_ou_mae']}: {flag['aviso']}")

        if graph.number_of_edges() == 0:
            st.info("Nenhuma relação com sujeito identificado neste conjunto — sem arestas pra mostrar.")
        else:
            html = render_graph_html(graph, cycles, temporal_flags)
            st.iframe(html, height=680)


if __name__ == "__main__":
    main()
