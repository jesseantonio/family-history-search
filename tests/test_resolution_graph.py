from src.graph.build import build_graph
from src.preprocessing.segment import _window_around
from src.resolution.clustering import DEFAULT_THRESHOLD, cluster_by_threshold, compute_all_pairs
from src.resolution.features import WEIGHTS, PersonMentionRecord, compute_score


def _mention(name, year=1909, occ="o1", people=(), places=()):
    return PersonMentionRecord(
        mention_id=f"{occ}::0", occurrence_id=occ, name_raw=name, name_normalized=name.lower(),
        source="ancora", year=year, year_precision="dia", places=frozenset(places),
        associated_people=frozenset(people), periodical="p", context_excerpt="", source_url="",
    )


def test_normalize_name_drops_titles():
    from src.resolution.features import normalize_name
    assert normalize_name("d. Ana Kraus") == normalize_name("Ana Kraus") == "ana kraus"
    assert normalize_name("Sr. Antônio Effting") == "antonio effting"
    assert normalize_name("Dr.") == "dr."      # só o título: mantém


def test_score_reaches_one():
    assert abs(sum(WEIGHTS.values()) - 1) < 1e-9
    a = _mention("Antonio Effting", people={"maria"}, places={"joinville"})
    b = _mention("Antonio Effting", people={"maria"}, places={"joinville"})
    assert abs(compute_score(a, b).total - 1.0) < 1e-9


def test_threshold_keeps_old_behaviour():
    # par que somava 0,56 na escala antiga (máx. 0,9) continua agrupado; 0,54 continua separado
    assert 0.56 / 0.9 >= DEFAULT_THRESHOLD > 0.54 / 0.9


def test_cluster_needs_more_than_the_name():
    # nome igual sozinho não basta (família grande, nomes repetidos); com pessoa em comum, agrupa
    ms = [_mention("Antonio Effting", occ="a", people={"maria"}),
          _mention("Antonio Effting", occ="b", people={"maria"}),
          _mention("Antonio Effting", occ="c"),
          _mention("Rosa Effting", year=1990, occ="d")]
    groups = cluster_by_threshold(len(ms), compute_all_pairs(ms))
    assert sorted(map(sorted, groups)) == [[0, 1], [2], [3]]


def test_graph_keeps_homonyms_in_different_clusters_apart():
    def occ(oid, pai):
        return {"id": oid, "source_url": "u", "year": 1909, "relations": [{
            "relation": "filho_de", "subject_raw": "Joao Effting", "target_raw": pai,
            "confidence": 0.7, "evidence": "e", "status": "extraida"}]}

    report = {"clusters_por_regra": [
        {"cluster_id": 0, "membros": [{"name_raw": "Joao Effting", "occurrence_id": "o1"}]},
        {"cluster_id": 1, "membros": [{"name_raw": "Joao Effting", "occurrence_id": "o2"}]},
    ]}
    g = build_graph([occ("o1", "Pedro Effting"), occ("o2", "Carlos Effting")], report)
    assert set(g.successors("cluster:0")) == {"nome:pedro effting"}
    assert set(g.successors("cluster:1")) == {"nome:carlos effting"}


def test_graph_skips_rejected_relation():
    o = {"id": "o1", "source_url": "u", "relations": [{
        "relation": "casou_com", "subject_raw": "Ana Effting", "target_raw": "Pedro Souza",
        "confidence": 0.7, "evidence": "e", "status": "extraida"}]}
    assert build_graph([o], review={"o1": {"relations": {"0": "rejeitada"}}}).number_of_edges() == 0


def test_window_around_surname():
    text = ("x " * 500) + "Faleceu o Sr. Antonio Effting ontem." + (" y" * 500)
    w = _window_around(text, "Effting", size=100)
    assert "Effting" in w and len(w) <= 100
    assert _window_around("nada aqui", "Effting") is None
    assert "EFFTÍNG" in _window_around("o Sr. EFFTÍNG chegou", "Effting")
