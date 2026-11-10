"""`_find_results` não pode depender de abas que não são dele: o Chrome congela abas em
segundo plano, e um `evaluate` numa aba congelada nunca retorna."""

from types import SimpleNamespace

from src.collector import browser_bn


class FrozenPage:
    """Aba congelada: a conexão nem sabe o endereço, e qualquer `evaluate` nela travaria o
    script para sempre (aqui só fica registrado, para o teste conferir que não houve)."""
    url = ""
    frames = []

    def __init__(self):
        self.evaluated = False

    def evaluate(self, *a, **k):
        self.evaluated = True
        return ""


class Page:
    def __init__(self, url, frames=()):
        self.url = url
        self.frames = list(frames)

    def evaluate(self, *a, **k):
        return ""


def _setup(*pages, own):
    ctx = SimpleNamespace(pages=list(pages))
    for pg in pages:
        pg.context = ctx
    own.context = ctx
    return own


def test_finds_results_tab_behind_frozen_tab():
    own = Page("https://memoria.bn.gov.br/hdb/periodico.aspx")
    frozen = FrozenPage()
    results = Page("https://memoria.bn.gov.br/DocReader/docmulti.aspx?pesq=Westrupp")
    _setup(own, frozen, results, own=own)
    assert browser_bn._find_results(own, timeout_s=2) is results
    assert not frozen.evaluated


def test_waiting_never_touches_foreign_tabs():
    # resultado ainda não abriu: o script espera, mas só consulta a própria aba
    own = Page("https://memoria.bn.gov.br/hdb/periodico.aspx")
    frozen = FrozenPage()
    _setup(frozen, own, own=own)
    try:
        browser_bn._find_results(own, timeout_s=1.5)
    except TimeoutError:
        pass
    assert not frozen.evaluated


def test_finds_results_in_frame():
    frame = SimpleNamespace(url="https://memoria.bn.gov.br/DocReader/docmulti.aspx")
    own = Page("https://memoria.bn.gov.br/hdb/periodico.aspx", frames=[frame])
    _setup(FrozenPage(), own, own=own)
    assert browser_bn._find_results(own, timeout_s=2) is frame


def test_salvar_locais(tmp_path):
    import json

    class Form:
        def evaluate(self, js):
            return ["Todos (27)", "SC ", "PR", ""]

    path = tmp_path / "raw" / browser_bn.LOCAIS_FILE
    browser_bn.salvar_locais(Form(), path)
    assert json.loads(path.read_text(encoding="utf-8"))["locais"] == ["SC", "PR"]

    class Broken:
        def evaluate(self, js):
            raise RuntimeError("site mudou")

    browser_bn.salvar_locais(Broken(), tmp_path / "outro.json")   # não interrompe a busca
    assert not (tmp_path / "outro.json").exists()


def test_only_bn_pages_are_inspected():
    assert browser_bn._is_bn_page(Page("https://memoria.bn.gov.br/x"))
    assert not browser_bn._is_bn_page(Page("https://www.google.com/search?q=x"))
    assert not browser_bn._is_bn_page(FrozenPage())
