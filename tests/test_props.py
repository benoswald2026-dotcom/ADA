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


def _rec_rows(pid, team, yards, targets, gid):
    return {"player_id": pid, "position": "WR", "game_id": gid, "team": team, "opponent_team": "X",
            "receiving_yards": float(yards), "targets": float(targets)}


def test_usage_prior_shrinks_a_star_toward_a_star_not_the_league_average():
    h = props.History("rec_yds")
    for i in range(40):   # league: depth receivers, 4 targets, 30 yards
        h.add_game([_rec_rows(f"d{i}", "AAA", 30, 4, f"g{i}")])
    for i in range(3):    # star: 10 targets, 110 yards
        h.add_game([_rec_rows("star", "SEA", 110, 10, f"s{i}")])
    base, _, _ = h.baseline("star", "SEA")
    old = props.USAGE_PRIOR_STATS
    try:
        props.USAGE_PRIOR_STATS = set()
        flat, _, _ = h.baseline("star", "SEA")
    finally:
        props.USAGE_PRIOR_STATS = old
    assert base > flat + 10          # shrunk toward ~10 targets' worth, not 4
    assert base < 110


def test_games_for_an_old_team_count_less(monkeypatch):
    monkeypatch.setattr(props, "OLD_TEAM_WEIGHT", 0.5)
    h = props.History("rec_yds")
    for i in range(4):
        h.add_game([_rec_rows("p", "MIA", 90, 8, f"m{i}")])
    h.add_game([_rec_rows("p", "DEN", 20, 8, "d0")])
    same_team, _, _ = h.baseline("p", "MIA")
    new_team, _, _ = h.baseline("p", "DEN")
    assert new_team < same_team      # the DEN game weighs more once he's a Bronco


def test_project_applies_accepted_qb_term():
    f = _fitted()
    f["terms"]["qb"] = {"coef": 0.4, "accepted": True}
    e = {"base": 50.0, "opp_x": 0.0, "team_x": 0.0, "qb_x": -0.15}   # backup starting
    assert props.project(e, f) == pytest.approx(50 * (1 + 0.4 * -0.15))


def test_qb_props_follow_the_listed_starter():
    import pandas as pd
    idx = pd.MultiIndex.from_tuples([("c1", "Caleb Williams", "QB"), ("r1", "D'Andre Swift", "RB")],
                                    names=["player_id", "player_display_name", "position"])
    usage = pd.Series([34.0, 15.0], index=idx)
    df = pd.DataFrame([{"player_id": "b1", "player_display_name": "Tyson Bagent", "position": "QB", "team": "CHI"},
                       {"player_id": "c1", "player_display_name": "Caleb Williams", "position": "QB", "team": "CHI"}])
    out = props._starting_qb_only(usage, "Tyson Bagent", "CHI", df)
    names = [k[1] for k in out.index]
    assert "Caleb Williams" not in names and "Tyson Bagent" in names and "D'Andre Swift" in names
    assert props._starting_qb_only(usage, None, "CHI", df).equals(usage)


def test_kalshi_prop_ladder_median_and_best_contract():
    strikes = [{"n": 50, "yes_ask": 0.62, "yes_bid": 0.60, "no_ask": 0.40},
               {"n": 60, "yes_ask": 0.51, "yes_bid": 0.49, "no_ask": 0.51},
               {"n": 70, "yes_ask": 0.40, "yes_bid": 0.38, "no_ask": 0.62}]
    assert props.kalshi_median(strikes) == pytest.approx(60.0)
    f = _fitted()
    best = props.best_kalshi_prop(strikes, 45.0, props.sd_for(45.0, f), f, trust=1.0)   # model expects fewer yards
    assert best["side"] == "NO" and best["edge"] > 0
    # trust 0 means defer to Kalshi's price entirely: no contract has a positive edge after the fee
    none = props.best_kalshi_prop(strikes, 45.0, props.sd_for(45.0, f), f, trust=0.0)
    assert none["edge"] < 0


def test_player_key_ignores_suffixes_and_punctuation():
    assert props.player_key("Luther Burden III") == props.player_key("Luther Burden")
    assert props.player_key("C.J. Stroud") == props.player_key("CJ Stroud")


def test_learn_props_trust_falls_when_model_gaps_fail(tmp_path, monkeypatch):
    import csv, pandas as pd
    log = tmp_path / "log.csv"
    rows = []
    for i in range(200):   # model always says 70% for 'N+', Kalshi says 50%, but it hits half the time
        rows.append({"logged_at": "x", "season": 2026, "game_id": f"G{i}", "kickoff": "2026-10-18T13:00:00-04:00",
                     "player_id": f"P{i}", "player": "X", "team": "KC", "stat": "rec_yds", "strike": 60, "side": "YES",
                     "ask": 0.5, "mid": 0.5, "p_model": 0.7, "p_used": 0.7, "edge": 0.18, "flag": 1})
    with open(log, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=props.PROPS_LOG_FIELDS); w.writeheader(); w.writerows(rows)
    df = pd.DataFrame([{"game_id": f"G{i}", "player_id": f"P{i}", "receiving_yards": 80 if i % 2 else 20,
                        "passing_yards": 0, "rushing_yards": 0, "receptions": 0} for i in range(200)])
    monkeypatch.setattr(props, "load_player_weeks", lambda a, b: df)
    out = props.learn_props(str(log), str(tmp_path / "w.json"))
    assert out["graded"] == 200 and out["trust"] < 0.3 and out["flag_record"] == {"W": 100, "L": 100}


def test_starter_qb_not_added_to_receiving_stats():
    """The starter is added only for stats QBs are projected for; before this fix every
    starting QB showed up with ~3 receptions and ~35 receiving yards."""
    import pandas as pd
    idx = pd.MultiIndex.from_tuples([("w1", "DJ Moore", "WR")], names=["player_id", "player_display_name", "position"])
    usage = pd.Series([8.0], index=idx)
    df = pd.DataFrame([{"player_id": "b1", "player_display_name": "Tyson Bagent", "position": "QB", "team": "CHI"}])
    out = props._starting_qb_only(usage, "Tyson Bagent", "CHI", df, props.STATS["receptions"]["pos"])
    assert [k[1] for k in out.index] == ["DJ Moore"]
    assert "Tyson Bagent" in [k[1] for k in props._starting_qb_only(usage, "Tyson Bagent", "CHI", df).index]


def test_calibration_spreads_bunched_projections():
    """If actual results run 1.5x the projection, calibration learns it."""
    ex = [{"season": 2020, "base": b, "actual": 1.5 * b - 5, "opp_x": 0.0, "team_x": 0.0, "qb_x": 0.0}
          for b in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0] * 20]
    f = props.fit(ex, [2020], calibrate=True)
    for base in (10.0, 40.0, 60.0):   # projections now land on the actual results
        assert abs(props.project({"base": base, "opp_x": 0.0, "team_x": 0.0, "qb_x": 0.0}, f) - (1.5 * base - 5)) < 1.0
    assert "calib" not in props.fit(ex, [2020])
