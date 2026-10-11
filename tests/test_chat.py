import chat


def _row(gid, home, away, p_home, mkt_home, raw_home=None, kalshi=None):
    return {"game": {"id": gid, "home": home, "away": away},
            "pred": {"home_prob": p_home, "raw_home_prob": raw_home if raw_home is not None else p_home},
            "market": {"home_prob": mkt_home, "home_ml_dec": 1.9, "away_ml_dec": 1.9},
            "kalshi": kalshi, "best": None, "flag": False,
            "sources": {"model": "model src", "market": "market src", "kalshi": "kalshi src"}}


def test_search_finds_benter_for_combining_model_and_market():
    ids = [c["id"] for c in chat.index().search("how should I combine my model with the market odds")]
    assert "benter-1994" in ids


def test_search_finds_kalshi_study_for_cheap_contracts():
    ids = [c["id"] for c in chat.index().search("why avoid cheap kalshi contracts longshot")]
    assert ids[0] == "burgi-deng-whelan-2025"


def test_fee_answer_uses_price_in_question():
    a = chat.answer("what is the kalshi fee at 55c?")
    assert any("$0.0173" in line for line in a["answer"])


def test_team_lookup_explains_gap_exclusion():
    rows = [_row(1, "Green Bay", "Chicago", 0.47, 0.47, raw_home=0.30)]
    a = chat.answer("what about Green Bay?", rows)
    assert any("left out" in f for f in a["facts"])
    assert "model src" in a["sources"]


def test_parlay_with_two_teams_is_priced():
    rows = [_row(1, "A Team", "B Team", 0.6, 0.6), _row(2, "C Team", "D Team", 0.5, 0.5)]
    a = chat.answer("parlay A Team and C Team", rows)
    assert any("chance to hit 30.0%" in line for line in a["answer"])


def test_empty_question_gives_suggestions():
    assert chat.answer("")["suggestions"]


def test_unknown_question_never_invents():
    a = chat.answer("zzqx blorf")
    assert a["citations"] == [] and a["suggestions"]
