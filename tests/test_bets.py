import pytest

from bets import find_bets


def row(gid, home, away, bet_home, market_home, raw_home=None, k_home=None, k_away=None, dec=(1.9, 1.9)):
    return {"game": {"id": gid, "home": home, "away": away},
            "pred": {"home_prob": bet_home, "raw_home_prob": raw_home if raw_home is not None else bet_home},
            "market": {"home_prob": market_home, "home_ml_dec": dec[0], "away_ml_dec": dec[1]},
            "kalshi": None if k_home is None else {"home": {"yes_ask": k_home}, "away": {"yes_ask": k_away}}}


def test_kalshi_cheaper_than_consensus_is_flagged():
    # Market says 60%, Kalshi sells YES at 52c: edge = 0.60 - 0.52 - fee(0.0175) ~ 6.3 pts
    b = find_bets([row(1, "A", "B", 0.60, 0.60, k_home=0.52, k_away=0.50)])
    top = b["kalshi"][0]
    assert top["team"] == "A" and top["flag"] is True
    assert top["edge"] == pytest.approx(0.60 - 0.52 - 0.07 * 0.52 * 0.48)


def test_gap_rule_uses_raw_model():
    # Bet prob equals market, but the raw model is 20 pts away -> excluded, never flagged
    b = find_bets([row(1, "A", "B", 0.60, 0.60, raw_home=0.80, k_home=0.50, k_away=0.52)])
    assert b["kalshi"][0]["flag"] is False
    assert b["excluded_gap"] == ["B @ A"]


def test_parlays_use_distinct_games_and_positive_ev_legs():
    rows = [row(1, "A", "B", 0.58, 0.55, dec=(1.95, 1.95)),
            row(2, "C", "D", 0.57, 0.55, dec=(1.95, 1.95)),
            row(3, "E", "F", 0.40, 0.45, dec=(2.1, 1.8))]
    b = find_bets(rows)
    for p in b["parlays"]:
        ids = [l["game_id"] for l in p["legs"]]
        assert len(ids) == len(set(ids))
        assert p["ev"] > 0
    assert any(len(p["legs"]) == 2 for p in b["parlays"])
