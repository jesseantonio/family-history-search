import json

import httpx
import pymupdf
import pytest

from src.collector.bn_client import is_pdf_bytes, make_occurrence_id
from src.collector.editions import EditionHit, filtrar_periodo, load_listagem, save_listagem
from src.collector.pdf_collect import coletar, download, norm, page_matches, publication_date


def _make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _ed(**kw):
    base = dict(bib="886173", jornal="Commercio de Joinville", ano=1909, edicao="00235", ocorrencias_bn=1)
    return EditionHit(**{**base, **kw})


# ---------------------------------------------------------------- ids
def test_occurrence_id_is_deterministic():
    a = make_occurrence_id("Effting", "886173", 1909, "00235", 2)
    assert a == make_occurrence_id("effting ", "886173", 1909, "00235", 2)
    assert a != make_occurrence_id("Effting", "886173", 1909, "00235", 3)


# ---------------------------------------------------------------- busca no texto da página
def test_page_matches_exact_hyphen_and_ocr_error():
    n = norm("Sautner")
    assert page_matches("o Sr. Sautner chegou", n)
    assert page_matches("o Sr. Saut-\nner chegou", n)
    assert page_matches("o Sr. Saurner chegou", n)        # 1 letra trocada pelo OCR
    assert not page_matches("o Sr. Mautner chegou", n)    # primeira letra diferente


def test_short_surname_only_exact():
    n = norm("Silva")
    assert page_matches("a familia Silva", n)
    assert not page_matches("a familia Salva", n)


def test_publication_date_header():
    assert publication_date("Joinville, Domingo, 3 de Janeiro de 1889\n...", 1889) == "1889-01-03"
    assert publication_date("Curityba, 3 de Mar�o de 1926", 1926) == "1926-03-03"
    assert publication_date("Joinville, 3 de Janeiro de 1888", 1889) is None   # ano diferente da pasta
    assert publication_date("sem data nenhuma", 1889) is None


# ---------------------------------------------------------------- listagem
def test_listagem_roundtrip(tmp_path):
    ed = _ed(status="ok", pasta="ano 1909\\edicao 00235", paginas_pdf=[2, 5])
    path = tmp_path / "listagem.csv"
    save_listagem([ed], "Effting", path)
    [back] = load_listagem(path)
    assert (back.pasta, back.status, back.paginas_pdf) == (ed.pasta, "ok", [2, 5])


def test_listagem_old_format(tmp_path):
    path = tmp_path / "old.csv"
    path.write_text(
        "termo,bib,jornal,ano,edicao,ocorrencias_edicao_BN,link_pdf,link_docreader,status,paginas_pdf\n"
        "Effting,886173,Jornal,1909,00235,3,x,y,pendente,\n", encoding="utf-8")
    [ed] = load_listagem(path)
    assert ed.ocorrencias_bn == 3 and ed.pasta == "" and ed.paginas_pdf == []


def test_filtrar_periodo():
    eds = [_ed(ano=1899), _ed(ano=1905), _ed(ano=1920)]
    assert [e.ano for e in filtrar_periodo(eds, 1900, 1910)] == [1905]
    assert len(filtrar_periodo(eds, None, None)) == 3


# ---------------------------------------------------------------- download
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_rejects_non_pdf(tmp_path):
    client = _client(lambda req: httpx.Response(200, text="<html>Just a moment...</html>"))
    with pytest.raises(RuntimeError):
        download(_ed(), tmp_path, client)
    assert not (tmp_path / "886173").exists() or not any((tmp_path / "886173").iterdir())


def test_download_replaces_invalid_cached_file(tmp_path):
    ed = _ed()
    cached = tmp_path / ed.bib / ed.pdf_name
    cached.parent.mkdir(parents=True)
    cached.write_text("<html>verificação</html>")
    pdf_bytes = b"%PDF-1.4 conteudo"
    path, baixou = download(ed, tmp_path, _client(lambda req: httpx.Response(200, content=pdf_bytes)))
    assert baixou and path.read_bytes() == pdf_bytes


def test_download_404(tmp_path):
    assert download(_ed(), tmp_path, _client(lambda req: httpx.Response(404))) == (None, False)


def test_is_pdf_bytes():
    assert is_pdf_bytes(b"%PDF-1.7\n...")
    assert not is_pdf_bytes(b"<!doctype html>")


# ---------------------------------------------------------------- coleta (PDFs já em cache: sem rede)
def test_coletar_from_cache(tmp_path):
    ed = _ed()
    _make_pdf(tmp_path / ed.bib / ed.pdf_name,
              ["Joinville, 3 de Novembro de 1909", "nada", "Faleceu o Sr. Antonio Effting"])
    concluidas = []
    occs = list(coletar([ed], "Effting", tmp_path, delay=5, ao_concluir=concluidas.append))
    assert [o.physical_page for o in occs] == [3]
    assert occs[0].id == make_occurrence_id("Effting", ed.bib, ed.ano, ed.edicao, 3)
    assert occs[0].publication_date == "1909-11-03"
    assert ed.status == "ok" and ed.paginas_pdf == [3]
    assert concluidas == [ed]


def test_coletar_skips_done_editions(tmp_path):
    ed = _ed()
    _make_pdf(tmp_path / ed.bib / ed.pdf_name, ["Effting"])
    assert list(coletar([ed], "Effting", tmp_path, ja_feitas={ed.key})) == []


def test_coletar_corrupt_pdf_does_not_crash(tmp_path):
    ed, ok = _ed(edicao="00001"), _ed(edicao="00002")
    bad = tmp_path / ed.bib / ed.pdf_name
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"%PDF-1.4 truncado")
    _make_pdf(tmp_path / ok.bib / ok.pdf_name, ["Effting"])
    occs = list(coletar([ed, ok], "Effting", tmp_path, delay=0))
    assert ed.status.startswith("erro_pdf") and not bad.exists()
    assert [o.edition for o in occs] == ["00002"]


# ---------------------------------------------------------------- retomada do pipeline
def test_pipeline_checkpoint(tmp_path):
    from src.pipeline import _carregar_checkpoint, _ocorrencias_ja_gravadas

    a, b = _ed(edicao="00001"), _ed(edicao="00002")
    ck = tmp_path / "coleta.jsonl"
    ck.write_text(
        json.dumps({"bib": a.bib, "ano": a.ano, "edicao": a.edicao, "status": "ok", "paginas_pdf": [1]}) + "\n"
        + json.dumps({"bib": b.bib, "ano": b.ano, "edicao": b.edicao, "status": "erro_download: x"}) + "\n"
        + '{"bib": "cortad', encoding="utf-8")
    feitas = _carregar_checkpoint(ck, [a, b])
    assert feitas == {a.key} and a.status == "ok" and a.paginas_pdf == [1]

    occ = tmp_path / "occ.jsonl"
    rec = {"id": "1", "bib": a.bib, "year": a.ano, "edition": a.edicao}
    other = {"id": "2", "bib": b.bib, "year": b.ano, "edition": b.edicao}
    occ.write_text("\n".join(json.dumps(r) for r in (rec, rec, other)) + "\n", encoding="utf-8")
    linhas = _ocorrencias_ja_gravadas(occ, feitas)
    assert [json.loads(l)["id"] for l in linhas] == ["1"]


# ---------------------------------------------------------------- coleta com download (rede simulada)
def _pdf_bytes(pages):
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _mock_network(monkeypatch, handler):
    import src.collector.pdf_collect as pc

    real_client = httpx.Client
    monkeypatch.setattr(pc.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


def test_coletar_parallel_keeps_order_and_limits_concurrency(tmp_path, monkeypatch):
    import threading
    import time

    eds = [_ed(edicao=f"{i:05d}") for i in range(1, 9)]
    ausente = eds[2].pdf_name
    lock, ativos, pico = threading.Lock(), [0], [0]

    def handler(req):
        with lock:
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
        time.sleep(0.05)
        with lock:
            ativos[0] -= 1
        if req.url.path.endswith(ausente):
            return httpx.Response(404)
        return httpx.Response(200, content=_pdf_bytes(["capa", "Faleceu o Sr. Effting"]))

    _mock_network(monkeypatch, handler)
    occs = list(coletar(eds, "Effting", tmp_path, delay=0, workers=3))

    assert [o.edition for o in occs] == [e.edicao for e in eds if e.pdf_name != ausente]
    assert 1 < pico[0] <= 3
    assert eds[2].status == "pdf_ausente_no_servidor"
    assert all(e.status == "ok" for i, e in enumerate(eds) if i != 2)


def test_coletar_non_pdf_without_browser_is_download_error(tmp_path, monkeypatch):
    _mock_network(monkeypatch, lambda req: httpx.Response(200, text="<html>Just a moment...</html>"))
    ed = _ed()
    assert list(coletar([ed], "Effting", tmp_path, delay=0)) == []
    assert ed.status.startswith("erro_download")
