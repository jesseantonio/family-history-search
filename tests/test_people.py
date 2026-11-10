from src.resolution.people import find_surname_people, group_people, person_of_name, same_token, ortho


def _names(text, surname="Westrupp"):
    return [(m.raw, m.given, m.title, m.suffix) for m in find_surname_people(text, surname)]


def _occ(i, text, year=1930):
    return {"id": f"o{i}", "context_excerpt": text, "year": year, "periodical": "Jornal", "event_type": "outro"}


# ---------------------------------------------------------------- menções
def test_ocr_split_name_is_joined():
    assert _names("felicitações v. exa GregO' rio Westrupp, Antonio")[0][:2] == ("GregO' rio Westrupp", ("gregorio",))


def test_stops_at_punctuation_conjunction_and_titles():
    assert _names("Avelino. Gresorio Westrupp Jopò")[0][1] == ("gresorio",)
    assert _names("Zoê e Antônio Westrupp")[0][1] == ("antonio",)
    assert _names("O Revmo. Pe. Grefório Westrupp pronuncia")[0][1:3] == (("greforio",), "Pe.")
    assert _names("Celso Westrupp Professor")[0][1] == ("celso",)
    assert _names("Titular ZOÊ LACERDA WESTRUPP")[0][1] == ("zoe", "lacerda")


def test_surname_only_and_institutions():
    assert _names("afirma Westrupp. Atualmente")[0][1] == ()
    assert _names("LAR ZOÊ LACERDA WESTRUPP")[0][1] == ()


def test_connector_and_suffix():
    raw, given, _, suffix = _names("Maria da Silva Westrupp Filho, comerciante")[0]
    assert given == ("maria", "silva") and suffix == "Filho" and raw == "Maria da Silva Westrupp Filho"


def test_surname_with_ocr_error():
    assert _names("Henrique Westrup, Vendelino")[0][1] == ("henrique",)
    assert _names("Nestcr Wcstrupp")[0][1] == ("nestcr",)


# ---------------------------------------------------------------- grafias
def test_token_equivalence():
    assert same_token(ortho("Gregório"), ortho("Gresorio"))
    assert same_token(ortho("Jregorio"), ortho("Gregorio"))
    assert same_token(ortho("Luiz"), ortho("Luis"))
    assert same_token(ortho("Theodoro"), ortho("Teodoro"))
    assert not same_token(ortho("Antonio"), ortho("Antonia"))     # gênero, não erro de OCR
    assert not same_token(ortho("Maria"), ortho("Marta"))         # nomes curtos: só exato


# ---------------------------------------------------------------- agrupamento
def test_user_example_is_one_person():
    texts = ["Gresorio Westrupp, José", "exa GregO' rio Westrupp, Antonio", "(Jregorio Westrupp; de",
             "e Gregorio Westrupp, porque", "Gregório Westrupp — Ituporanga"]
    res = group_people([_occ(i, t) for i, t in enumerate(texts)], "Westrupp")
    assert len(res["people"]) == 1
    p = res["people"][0]
    assert p["name"] in ("Gregorio Westrupp", "Gregório Westrupp")
    assert p["mentions"] == 5 and len(p["occurrences"]) == 5


def test_short_name_joins_unique_full_name():
    texts = ["maestro Sérgio Westrupp.", "Sérgio Luiz Westrupp, regente", "Sérgio Luis Westrupp.", "Sérgio Westrupp e"]
    res = group_people([_occ(i, t) for i, t in enumerate(texts)], "Westrupp")
    assert [p["name"] for p in res["people"]] == ["Sérgio Luiz Westrupp"]


def test_short_name_stays_apart_when_ambiguous():
    texts = ["Antônio Getúlio Westrupp, diretor", "Antônio Carlos Westrupp, vereador", "Antônio Westrupp, comerciante"]
    names = {p["name"] for p in group_people([_occ(i, t) for i, t in enumerate(texts)], "Westrupp")["people"]}
    assert names == {"Antônio Getúlio Westrupp", "Antônio Carlos Westrupp", "Antônio Westrupp"}


def test_different_people_stay_apart():
    texts = ["Antonio Westrupp, lavrador", "Antonia Westrupp, professora", "Celso Westrupp.", "Nelson Westrupp."]
    res = group_people([_occ(i, t) for i, t in enumerate(texts)], "Westrupp")
    assert len(res["people"]) == 4


def test_manual_split_and_merge():
    texts = ["Gregorio Westrupp, José", "Gresorio Westrupp, José", "Celso Westrupp."]
    occs = [_occ(i, t) for i, t in enumerate(texts)]
    split = group_people(occs, "Westrupp", splits=["gresorio"])
    assert len(split["people"]) == 3
    merged = group_people(occs, "Westrupp", merges=[["gregorio", "celso"]])
    assert len(merged["people"]) == 1


def test_by_occurrence_and_person_of_name():
    occs = [_occ(1, "Gregorio Westrupp e Celso Westrupp."), _occ(2, "Gresorio Westrupp.")]
    res = group_people(occs, "Westrupp")
    assert len(res["by_occurrence"]["o1"]) == 2
    gid = person_of_name("Gresorio Westrupp", "Westrupp", res)
    assert gid and gid == person_of_name("Gregório Westrupp", "Westrupp", res)
    assert person_of_name("Maria Souza", "Westrupp", res) is None
