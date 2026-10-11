import numpy as np
import pytest

import props


def _fitted():
    return {"terms": {"bias": {"coef": 0.0, "accepted": False}, "opponent": {"coef": 0.5, "accepted": True},
                      "team_points": {"coef": 0.2, "accepted": False}},
            "sd": [5.0, 0.3], "zq": list(np.linspace(-3, 3, len(props.ZQ_LEVELS)))}


def test_project_applies_only_accepted_terms():
    e = {"base": 100.0, "opp_x": 0.2, "team_x": 0.5}
    assert props.project(e, _fitted()) == pytest.approx(100 * (1 + 0.5 * 0.2))   # team term ignored


def test_p_over_is_half_at_median_and_monotonic():
    f = _fitted()
    sd = props.sd_for(100, f)
    med = props.quantile(0.5, 100, sd, f)
    assert props.p_over(med, 100, sd, f) == pytest.approx(0.5, abs=0.01)
    assert props.p_over(80, 100, sd, f) > props.p_over(120, 100, sd, f)


def test_implied_team_points():
    # total 44, home favored by 4 -> home 24, away 20
    assert props.implied_team_points(44, -4, True) == pytest.approx(24)
    assert props.implied_team_points(44, -4, False) == pytest.approx(20)


def test_history_baseline_has_no_lookahead():
    h = props.History("rush_yds")
    rows = [{"player_id": "p1", "position": "RB", "game_id": "g1", "opponent_team": "X", "rushing_yards": 100.0, "carries": 20}]
    before, _, _ = h.baseline("p1")
    h.add_game(rows)
    after, _, _ = h.baseline("p1")
    assert before != after          # the game only counts once it has been added


def test_parse_prop_market_devigs_at_consensus_line():
    event = {"bookmakers": [
        {"markets": [{"key": "player_rush_yds", "outcomes": [
            {"name": "Over", "description": "Bijan Robinson", "point": 74.5, "price": -110},
            {"name": "Under", "description": "Bijan Robinson", "point": 74.5, "price": -110}]}]},
        {"markets": [{"key": "player_rush_yds", "outcomes": [
            {"name": "Over", "description": "Bijan Robinson", "point": 74.5, "price": -120},
            {"name": "Under", "description": "Bijan Robinson", "point": 74.5, "price": 100}]}]},
    ]}
    q = props.parse_prop_market(event, "player_rush_yds")["bijan robinson"]
    assert q["line"] == 74.5 and q["books"] == 2
    assert 0.5 < q["p_over"] < 0.53
