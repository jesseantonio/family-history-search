import json

import pytest
from starlette.testclient import TestClient

from src.web import server

NAME = "occurrences_region_effting_extracted.jsonl"


def _occ(i, text, rels, year=1931):
    return {"id": f"o{i}", "query": "Effting", "bib": "1", "periodical": "Jornal", "year": year,
            "edition": f"{i:05d}", "physical_page": 1, "source_url": "u", "publication_date": None,
            "context_excerpt": text, "ocr_raw": "PÁGINA INTEIRA", "text_normalized": "PÁGINA INTEIRA",
            "event_type": "outro", "dates": [], "relations": rels}


def _rel(rel, s, t, status="extraida"):
    return {"relation": rel, "subject_raw": s, "target_raw": t, "target_kind": None,
            "evidence": f"{s} {rel} {t}", "confidence": 0.7, "status": status}


@pytest.fixture
def client(tmp_path, monkeypatch):
    processed, annotations = tmp_path / "processed", tmp_path / "annotations"
    processed.mkdir()
    rows = [
        _occ(1, "José Effting, filho de d. Ana Kraus", [_rel("filho_de", "José Effting", "d. Ana Kraus"),
                                                       _rel("residente_em", "José Effting", "Joinville")]),
        _occ(2, "José Effting casou com Maria Schmitt", [_rel("casou_com", "José Effting", "Maria Schmitt")], 1958),
    ]
    (processed / NAME).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (processed / "occurrences_region_vazio_extracted.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "PROCESSED_DIR", processed)
    monkeypatch.setattr(server, "ANNOTATIONS_DIR", annotations)
    server._cache.clear()
    return TestClient(server.app)


def test_datasets_skip_empty(client):
    ds = client.get("/api/datasets").json()
    assert [d["name"] for d in ds] == [NAME]
    assert ds[0]["surname"] == "Effting" and ds[0]["count"] == 2


def test_occurrences_are_slim(client):
    data = client.get(f"/api/datasets/{NAME}/occurrences").json()
    o = data["occurrences"][0]
    assert "ocr_raw" not in o and "text_normalized" not in o
    assert o["date_display"] == "1931" and o["date_is_event"] is False


def test_review_roundtrip_and_export(client):
    r = client.post(f"/api/datasets/{NAME}/review", json={"occurrence_id": "o1", "status": "aprovado"})
    assert r.status_code == 200
    assert client.get(f"/api/datasets/{NAME}/occurrences").json()["review"]["o1"]["status"] == "aprovado"
    csv_text = client.get(f"/api/datasets/{NAME}/export.csv").text
    assert "o1" in csv_text and "o2" not in csv_text
    assert client.post(f"/api/datasets/{NAME}/review", json={"occurrence_id": "o1", "status": "x"}).status_code == 400


def test_tree_people_and_facts(client):
    t = client.get(f"/api/datasets/{NAME}/tree").json()
    labels = {n["label"] for n in t["nodes"]}
    assert labels == {"José Effting", "Ana Kraus", "Maria Schmitt"}   # título removido; lugar não vira pessoa
    jose = next(n for n in t["nodes"] if n["label"] == "José Effting")
    assert jose["bears_surname"] and jose["facts"][0]["value"] == "Joinville"
    assert {e["relation"] for e in t["edges"]} == {"filho_de", "casou_com"}


def test_relation_review_reaches_tree(client):
    client.post(f"/api/datasets/{NAME}/review", json={"occurrence_id": "o2", "relation_index": 0, "status": "rejeitada"})
    t = client.get(f"/api/datasets/{NAME}/tree").json()
    assert {e["relation"] for e in t["edges"]} == {"filho_de"}


def test_bad_names_and_inputs(client):
    assert client.get("/api/datasets/..%2Fsecret_extracted.jsonl/tree").status_code == 404
    assert client.get(f"/api/datasets/{NAME}/export.exe").status_code == 404
    assert client.post("/api/search", json={"surname": "", "uf": "SC"}).status_code == 400
    assert client.post("/api/search", json={"surname": "Effting", "uf": "Santa"}).status_code == 400
    assert client.get("/api/search").status_code == 200


def test_people_endpoint_and_adjustments(client):
    people = client.get(f"/api/datasets/{NAME}/people").json()
    assert [p["name"] for p in people["people"]] == ["José Effting"]
    assert set(people["by_occurrence"]) == {"o1", "o2"}
    r = client.post(f"/api/datasets/{NAME}/people", json={"action": "bogus"})
    assert r.status_code == 400
    # separar a única grafia não muda nada; juntar consigo mesma é inválido
    assert client.post(f"/api/datasets/{NAME}/people", json={"action": "merge", "a": "jose", "b": "jose"}).status_code == 400
    r = client.post(f"/api/datasets/{NAME}/people", json={"action": "split", "key": "jose"})
    assert r.status_code == 200 and len(r.json()["people"]) == 1
    r = client.post(f"/api/datasets/{NAME}/people", json={"action": "reset", "key": "jose"})
    assert r.status_code == 200


def test_tree_merges_spellings_of_same_person(client, tmp_path):
    rows = [
        _occ(1, "Gregorio Effting, filho de Pedro Effting", [_rel("filho_de", "Gregorio Effting", "Pedro Effting")]),
        _occ(2, "Gresorio Effting casou com Ana Kraus", [_rel("casou_com", "Gresorio Effting", "Ana Kraus")]),
    ]
    path = server.PROCESSED_DIR / NAME
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    t = client.get(f"/api/datasets/{NAME}/tree").json()
    gregorio = [n for n in t["nodes"] if "Gre" in n["label"]]
    assert len(gregorio) == 1 and gregorio[0]["person"]
    assert {e["relation"] for e in t["edges"]} == {"filho_de", "casou_com"}


def test_wide_context_falls_back_to_page_text(client):
    r = client.get(f"/api/datasets/{NAME}/occurrences/o1/context")
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["source"] == "pagina" and ctx["paragraphs"]
    assert client.get(f"/api/datasets/{NAME}/occurrences/nao-existe/context").status_code == 404


def test_person_search_validation_and_command(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_search", lambda *a, **k: calls.append((a, k)))
    ok = {"kind": "pessoa", "nome": "Carlos", "surname": "Barletta", "uf": "SC", "modo": "literal"}
    assert client.post("/api/search", json={**ok, "nome": ""}).status_code == 400
    assert client.post("/api/search", json={**ok, "modo": "aproximado"}).status_code == 400
    assert client.post("/api/search", json={**ok, "kind": "outra"}).status_code == 400
    client.post("/api/search", json=ok)
    assert calls[-1][1] == {"nome": "Carlos", "modo": "literal"}
    client.post("/api/search", json={"surname": "Effting", "uf": "SC"})          # família, como antes
    assert calls[-1][1] == {"nome": None, "modo": "inteligente"}


def test_person_dataset_label(client):
    row = {**_occ(9, "Carlo Barleta chegou", []), "query": "Barletta", "person_query": "Carlos Barletta",
           "search_mode": "inteligente", "match_text": "Carlo Barleta"}
    (server.PROCESSED_DIR / "occurrences_pessoa_carlos-barletta_extracted.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8")
    ds = {d["name"]: d for d in client.get("/api/datasets").json()}
    d = ds["occurrences_pessoa_carlos-barletta_extracted.jsonl"]
    assert d["person"] == "Carlos Barletta" and d["scope"] == "Busca de pessoa · inteligente"
    occ = client.get("/api/datasets/occurrences_pessoa_carlos-barletta_extracted.jsonl/occurrences").json()
    assert occ["occurrences"][0]["match_text"] == "Carlo Barleta"


def test_locais_default_and_saved(client, tmp_path):
    d = client.get("/api/locais").json()
    assert d["fonte"] == "padrao" and d["padrao"] == "SC" and len(d["locais"]) == 27
    sc = next(o for o in d["locais"] if o["value"] == "SC")
    assert sc["label"] == "Santa Catarina" and sc["sigla"] == "SC"
    # depois de uma busca, vale a lista que o próprio memoria.bn ofereceu
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "memoria_locais.json").write_text(
        json.dumps({"locais": ["SC", "PR", "Portugal"]}), encoding="utf-8")
    d = client.get("/api/locais").json()
    assert d["fonte"] == "memoria.bn"
    assert [o["label"] for o in d["locais"]] == ["Paraná", "Portugal", "Santa Catarina"]
    assert next(o for o in d["locais"] if o["value"] == "Portugal")["sigla"] == ""


def test_search_accepts_only_known_locais(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_search", lambda *a, **k: calls.append(a))
    assert client.post("/api/search", json={"surname": "Silva", "uf": "XX"}).status_code == 400
    client.post("/api/search", json={"surname": "Silva", "uf": "pr"})
    assert calls[-1][1] == "PR"
    client.post("/api/search", json={"surname": "Silva"})                       # sem estado: SC
    assert calls[-1][1] == "SC"


def test_cancel_running_search(client, tmp_path, monkeypatch):
    import subprocess
    import sys

    log = tmp_path / "busca_x.log"
    log_file = log.open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=log_file)
    monkeypatch.setattr(server, "_search", {"proc": proc, "log_path": log, "log_file": log_file,
                                            "surname": "Westrupp", "started": 0})
    try:
        assert client.get("/api/search").json()["state"] == "running"
        r = client.post("/api/search/cancel")
        assert r.status_code == 200 and r.json()["state"] == "cancelled"
        assert proc.poll() is not None
        assert client.post("/api/search/cancel").status_code == 409   # nada mais rodando
    finally:
        if proc.poll() is None:
            proc.kill()
        log_file.close()


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Family History Search" in r.text
