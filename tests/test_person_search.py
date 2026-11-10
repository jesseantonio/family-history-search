"""Busca de uma pessoa (nome + sobrenome), literal ou inteligente."""

import json

import pytest

from src.collector.bn_client import make_occurrence_id
from src.collector.pdf_collect import coletar
from src.pipeline import output_base, person_slug
from src.preprocessing.segment import _find_matching_blocks
from src.resolution.people import find_person, surname_variants
from tests.test_collector import _ed, _make_pdf

TEXT = ("Recebemos a visita do Sr. Carlo Barleta, e de Carlos Alberto Barletta. Também CARLOS BARLETTA "
        "e João Carlos Barletta; Carla Barletta; Carlos Bar-\nletta.")


def _raws(text, smart):
    return [m.raw for m in find_person(text, "Carlos", "Barletta", smart=smart)]


def test_literal_only_as_typed():
    found = _raws(TEXT, smart=False)
    assert found == ["CARLOS BARLETTA", "João Carlos Barletta", "Carlos Barletta"]


def test_smart_accepts_ocr_spellings_and_middle_names():
    found = _raws(TEXT, smart=True)
    assert "Carlo Barleta" in found and "Carlos Alberto Barletta" in found
    assert all("Carla" not in r for r in found)            # gênero não é erro de OCR
    assert all(not r.startswith("Também") for r in found)  # palavra de início de frase não é nome


def test_accents_and_case_do_not_matter_in_literal():
    assert _raws("o sr. CÁRLOS BÁRLETTA chegou", smart=False) == ["CÁRLOS BÁRLETTA"]


def test_compound_given_name():
    text = "Maria Helena da Silva Barletta e Maria Barletta"
    # literal: o texto não diz "Maria Helena Barletta"; com o nome completo, acha
    assert find_person(text, "Maria Helena", "Barletta", smart=False) == []
    assert [m.raw for m in find_person(text, "Maria Helena da Silva", "Barletta", smart=False)] == \
        ["Maria Helena da Silva Barletta"]
    smart = [m.raw for m in find_person(text, "Maria Helena", "Barletta", smart=True)]
    assert smart == ["Maria Helena da Silva Barletta"]     # "Maria Barletta" não tem o Helena


def test_surname_variants_for_bn_search():
    assert surname_variants("Barletta") == ["Barletta", "Barleta"]
    assert surname_variants("Barleta") == ["Barleta", "Barletta"]
    assert surname_variants("Theodoro")[:2] == ["Theodoro", "Teodoro"]
    assert surname_variants("Silva") == ["Silva"]
    assert len(surname_variants("Westrupp")) <= 3 and all(v[0].isupper() for v in surname_variants("Westrupp"))


def test_file_names_do_not_clash_with_family_search():
    assert output_base("Barletta") == "region_barletta"
    assert output_base("Barletta", "Carlos") == "pessoa_carlos-barletta"
    assert person_slug("José Maria", "Ávila") == "jose-maria-avila"


@pytest.mark.parametrize("smart,expected_pages", [(True, [1, 2]), (False, [3])])
def test_coletar_keeps_only_pages_with_the_person(tmp_path, smart, expected_pages):
    ed = _ed()
    _make_pdf(tmp_path / ed.bib / ed.pdf_name, [
        "Chegou o Sr. Carlo Barleta de Joinville.",
        "Casou-se Carlos Alberto Barletta.",
        "Partiu Carlos Barletta. Pedro Barletta ficou.",
        "Pedro Barletta, comerciante.",
    ])
    query = "Carlos Barletta"

    def match(text):
        found = find_person(text, "Carlos", "Barletta", smart=smart)
        return found[0].raw if found else None

    person = {"query": query, "mode": "inteligente" if smart else "literal", "match": match}
    occs = list(coletar([ed], "Barletta", tmp_path, delay=0, person=person))
    if smart:   # o inteligente também acha a página 3 ("Partiu Carlos Barletta")
        expected_pages = [1, 2, 3]
    assert [o.physical_page for o in occs] == expected_pages
    o = occs[0]
    assert o.person_query == query and o.query == "Barletta" and o.match_text
    assert o.id == make_occurrence_id(query, ed.bib, ed.ano, ed.edicao, o.physical_page)
    assert o.id != make_occurrence_id("Barletta", ed.bib, ed.ano, ed.edicao, o.physical_page)


def test_segment_finds_block_by_name_as_read():
    blocks = [(0, 0, 100, 20, "Pedro Barletta, comerciante.", 0, 0),
              (0, 30, 100, 50, "Chegou o Sr. Carlo\nBarleta de Joinville.", 1, 0)]
    found = _find_matching_blocks(blocks, "Carlo Barleta")
    assert found and found[0][1].startswith("Chegou")
