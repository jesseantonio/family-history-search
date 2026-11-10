import pytest

from src.extraction.entities import find_surname_person
from src.extraction.events import classify_event
from src.extraction.relations import extract_relations


# ---------------------------------------------------------------- eventos
def test_obituary_mentioning_birth_is_obito():
    ev = classify_event("Faleceu hontem o Sr. Antonio Effting, nascido em 1850.")
    assert ev.event_type == "obito"
    assert ev.confidence < 0.7  # dois tipos citados: confiança menor


def test_majority_wins():
    ev = classify_event("O casamento de Maria. Casou-se na matriz. Nasceu em 1880.")
    assert ev.event_type == "casamento"


def test_overlapping_keywords_count_once():
    # "casou-se" contém "casou": não pode virar 2 contra 1 em cima do óbito
    ev = classify_event("casou-se em 1880 e faleceu em 1920")
    assert ev.event_type == "obito"


def test_keyword_only_at_word_start():
    # "desfaleceu" (desmaiou) contém "faleceu", mas não é óbito
    assert classify_event("a moça desfaleceu de cansaço").event_type == "outro"
    assert classify_event("O natalicio do Sr. Effting").event_type == "nascimento"


def test_no_keyword_is_outro():
    ev = classify_event("Chegou de viagem o Sr. Effting.")
    assert ev.event_type == "outro" and ev.evidence is None


# ---------------------------------------------------------------- relações
def test_filho_de_two_parents():
    rels = extract_relations("Pedro Effting, filho de José Effting e Maria Schmitt, nasceu.")
    alvos = {r.target_raw for r in rels if r.relation == "filho_de"}
    assert alvos == {"José Effting", "Maria Schmitt"}
    assert all(r.subject_raw == "Pedro Effting" for r in rels if r.relation == "filho_de")


def test_filho_de_with_abbreviated_title():
    rels = extract_relations("Juvenal Pedro, filho de Pedro Kupchak e de d. Helena Kupchak. Maria Thereza")
    assert {r.target_raw for r in rels} == {"Pedro Kupchak", "d. Helena Kupchak"}


def test_subject_does_not_cross_sentence():
    rels = extract_relations("de d. Stella Bassani. Juvenal Pedro, filho de Pedro Kupchak.")
    assert rels[0].subject_raw == "Juvenal Pedro"


def test_subject_without_leading_connector():
    rels = extract_relations("Parabéns a Pedro, e Samantha Effting, filha de Zita.")
    assert rels[0].subject_raw == "Samantha Effting"


def test_subject_skips_leading_verb():
    rels = extract_relations("Nasceu Pedro Effting, filho de José Effting.")
    assert rels[0].subject_raw == "Pedro Effting"


def test_subject_stops_at_conjunction():
    rels = extract_relations("filho de Antonio Effting e de d. Ana Kraus, residente em Joinville.")
    res = [r for r in rels if r.relation == "residente_em"][0]
    assert "Antonio" not in (res.subject_raw or "")


def test_person_target_cut_at_non_name():
    rels = extract_relations("Rosa Effting casou com Carlos Souza na igreja de São Bento.")
    assert rels[0].target_raw == "Carlos Souza"


def test_place_target_not_trimmed():
    rels = extract_relations("Pedro Effting, residente em São Bento do Sul.")
    assert rels[0].target_raw == "São Bento do Sul"


def test_filho_de_idiom_excluded():
    assert extract_relations("Pedro, filho de criação do casal") == []


def test_pronoun_subject_dropped():
    rels = extract_relations("Ele, filho de José Effting, chegou.")
    assert rels and rels[0].subject_raw is None


# ---------------------------------------------------------------- pessoa âncora
def test_find_surname_person_expands_name():
    p = find_surname_person("Chegou o Sr. Antonio Effting Junior, de Joinville.", "Effting")
    assert p is not None and "Antonio Effting Junior" in p.name_raw


def test_find_surname_person_stops_at_list_separator():
    p = find_surname_person("Pedro Silva, Antonio Effting e outros", "Effting")
    assert p is not None and "Pedro" not in p.name_raw


def test_find_surname_person_absent():
    assert find_surname_person("nada aqui", "Effting") is None


# ---------------------------------------------------------------- pipeline
def test_relations_come_from_excerpt_not_whole_page():
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("pt_core_news_sm")
    except OSError:
        pytest.skip("modelo pt_core_news_sm não instalado")
    from src.extraction.pipeline import extract_from_occurrence

    occ = {
        "id": "x", "query": "Effting", "year": 1909, "publication_date": "1909-11-10",
        "context_excerpt": "Faleceu hontem o Sr. Antonio Effting.",
        # outra notícia da mesma página, longe do sobrenome
        "text_normalized": "Faleceu hontem o Sr. Antonio Effting. ... João Souza, filho de Pedro Souza, partiu.",
    }
    out = extract_from_occurrence(occ)
    assert out["relations"] == []
    assert out["event_type"] == "obito"
    assert [d["date_value"] for d in out["dates"]] == ["1909-11-09"]
