from src.extraction.dates import extract_dates

PUB = "1909-11-10"  # uma quarta-feira


def _values(text, pub=None, ref_year=None):
    return [(d.raw_text, d.date_value, d.precision, d.resolved_from) for d in extract_dates(text, pub, ref_year)]


def test_full_date_with_accent():
    assert _values("faleceu a 3 de março de 1909") == [("3 de março de 1909", "1909-03-03", "dia", None)]


def test_full_date_with_ocr_replacement_char():
    assert _values("3 de mar�o de 1909")[0][1] == "1909-03-03"


def test_ordinal_first_day():
    assert _values("Joinville, 1.º de Janeiro de 1889")[0][1] == "1889-01-01"
    assert _values("1º de maio de 1900")[0][1] == "1900-05-01"


def test_month_year_not_duplicated_inside_full_date():
    assert len(extract_dates("em 3 de novembro de 1909")) == 1
    assert _values("novembro de 1909") == [("novembro de 1909", "1909-11", "mes", None)]


def test_offsets_point_to_original_text():
    text = "Ontem, terça-feira última, às 3 horas"
    for d in extract_dates(text, PUB):
        assert text[d.start_offset:d.end_offset] == d.raw_text


def test_relative_words_resolved_from_publication():
    assert _values("hontem", PUB)[0][1:] == ("1909-11-09", "dia", "publicacao")
    assert _values("amanhã", PUB)[0][1] == "1909-11-11"
    assert _values("ante-hontem", PUB)[0][1] == "1909-11-08"


def test_relative_weekdays():
    assert _values("terça-feira última", PUB)[0][1] == "1909-11-09"
    assert _values("s�bado passado", PUB)[0][1] == "1909-11-06"
    assert _values("sabbado passado", PUB)[0][1] == "1909-11-06"
    assert _values("domingo próximo", PUB)[0][1] == "1909-11-14"
    # mesmo dia da semana da publicação: "quarta-feira última" é a da semana anterior
    assert _values("quarta-feira última", PUB)[0][1] == "1909-11-03"


def test_current_and_last_month():
    assert _values("no dia 5 do corrente", PUB)[0][1] == "1909-11-05"
    assert _values("do corrente mez", PUB)[0][1:3] == ("1909-11", "mes")
    assert _values("dia 28 do mês passado", PUB)[0][1] == "1909-10-28"
    assert _values("dia 28 do mez passado", "1910-01-05")[0][1] == "1909-12-28"


def test_relative_without_publication_stays_unresolved():
    d = extract_dates("hontem")[0]
    assert d.is_relative and d.year is None and d.precision == "relativa"


def test_date_without_year_uses_publication_year():
    assert _values("a 28 de outubro", PUB)[0][1:] == ("1909-10-28", "dia", "publicacao")
    # mais de 6 meses depois da publicação: ano anterior
    assert _values("a 28 de dezembro", "1910-01-05")[0][1] == "1909-12-28"
    # sem data de publicação, o ano da edição completa a data
    assert _values("a 3 de maio", None, 1901)[0][1] == "1901-05-03"


def test_invalid_day_is_not_resolved():
    d = extract_dates("a 31 de fevereiro", PUB)[0]
    assert d.year is None and d.date_value is None
