import numpy as np
import pytest

import nfl


def test_combo_market_mode_returns_market_exactly():
    c = {"mode": "market", "a0": 0, "a1": 1, "a2": 0, "market_only": {"a0": 0, "a1": 1}}
    assert nfl.combo_prob(c, 0.63, 0.80) == pytest.approx(0.63)


def test_combo_full_mode_math():
    c = {"mode": "model+market", "a0": 0.0, "a1": 0.5, "a2": 0.5, "market_only": {}}
    # equal logit weights on 0.6 and 0.6 -> 0.6
    assert nfl.combo_prob(c, 0.6, 0.6) == pytest.approx(0.6)


def test_logistic_fit_recovers_coefficients():
    rng = np.random.default_rng(1)
    x = rng.normal(size=20000)
    y = (rng.random(20000) < 1 / (1 + np.exp(-(0.3 + 1.5 * x)))).astype(float)
    b, se, _ = nfl._logistic_fit(np.column_stack([np.ones_like(x), x]), y)
    assert b[0] == pytest.approx(0.3, abs=0.06) and b[1] == pytest.approx(1.5, abs=0.08)


def _rec(season, f, resid):
    return {"season": season, "f": f, "margin_actual": resid, "base_margin": 0.0,
            "total_actual": 0.0, "base_total": 0.0}


def test_feature_gate_rejects_noise_and_keeps_strong_signal():
    rng = np.random.default_rng(2)
    recs = []
    for _ in range(3000):
        f = {"rest": rng.normal(), "tz": rng.normal(), "form": rng.normal(), "wind_x": 0.0, "cold": 0.0}
        recs.append(_rec(2020, f, 2.0 * f["rest"] + rng.normal(0, 13)))   # only rest is real
    env = nfl.fit_environment(recs, [2020])
    assert env["margin"]["rest"]["accepted"] is True
    assert env["margin"]["tz"]["accepted"] is False and env["applied"]["tz"] == 0.0


def test_kickoff_parses_eastern_time():
    k = nfl._kickoff({"gameday": "2026-10-15", "gametime": "20:15"})
    assert k.isoformat() == "2026-10-15T20:15:00-04:00"
    assert nfl._kickoff({"gameday": "2026-10-15", "gametime": None}) is None


def _row(gid, ask):
    return {"game": {"id": gid, "start": "2026-10-18T13:00:00-04:00", "home_code": "NE", "away_code": "NYJ"},
            "market": {"home_prob": 0.73, "prob_source": "Pinnacle (sharp)", "sharp_prob": 0.73,
                       "consensus_prob": 0.72, "home_spread": -6.5, "total": 41.5},
            "kalshi": {"home": {"yes_ask": ask}, "away": {"yes_ask": 0.25}}}


def test_log_snapshot_appends_and_skips_already_logged_fetch(tmp_path):
    import csv
    path = str(tmp_path / "snap.csv")
    assert nfl.log_snapshot([_row("G1", 0.74)], "t1", "k1", path=path) == 1
    assert nfl.log_snapshot([_row("G1", 0.74)], "t1", "k1", path=path) == 0   # same cached fetch
    assert nfl.log_snapshot([_row("G1", 0.76)], "t1", "k2", path=path) == 1   # new Kalshi fetch
    rows = list(csv.DictReader(open(path)))
    assert [r["kalshi_home_ask"] for r in rows] == ["0.74", "0.76"]
    assert rows[0]["prob_source"] == "Pinnacle (sharp)"


def test_pbp_game_rows_drops_garbage_time_and_clips_epa():
    import pandas as pd
    pbp = pd.DataFrame([
        # game G: home offense KC, away offense BUF
        {"game_id": "G", "posteam": "KC", "defteam": "BUF", "epa": 0.5, "wp": 0.5, "pass": 1, "rush": 0},
        {"game_id": "G", "posteam": "KC", "defteam": "BUF", "epa": 9.0, "wp": 0.6, "pass": 1, "rush": 0},  # clipped to 4
        {"game_id": "G", "posteam": "KC", "defteam": "BUF", "epa": 3.0, "wp": 0.97, "pass": 0, "rush": 1},  # garbage time
        {"game_id": "G", "posteam": "BUF", "defteam": "KC", "epa": -0.2, "wp": 0.4, "pass": 0, "rush": 1},
        {"game_id": "G", "posteam": "BUF", "defteam": "KC", "epa": 1.0, "wp": 0.4, "pass": 0, "rush": 0},  # punt etc.
    ])
    rows = {r["team"]: r for r in nfl.pbp_game_rows(pbp)["G"]}
    assert rows["KC"]["off_epa"] == pytest.approx((0.5 + 4.0) / 2)
    assert rows["BUF"]["off_epa"] == pytest.approx(-0.2)
    assert rows["KC"]["def_epa"] == pytest.approx(-0.2)


def _epa_records(n, true_w, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        elo, epa = rng.normal(0, 6), rng.normal(0, 9)
        out.append({"season": 2016, "elo_margin": elo, "epa_margin": epa, "epa_ramp": 1.0,
                    "margin_actual": elo + true_w * (epa - elo) + rng.normal(0, 3)})
    return out


def test_fit_epa_weight_replaces_prior_only_when_data_clearly_disagrees():
    far = nfl.fit_epa_weight(_epa_records(2000, 0.1), [2016])
    assert far["accepted"] and far["w"] == pytest.approx(0.1, abs=0.02)
    near = nfl.fit_epa_weight(_epa_records(40, 0.42), [2016])
    assert not near["accepted"] and near["w"] == nfl.config.NFL.EPA_WEIGHT_MAX


def test_set_base_margins_uses_weight_and_ramp():
    recs = [{"elo_margin": 2.0, "epa_margin": 6.0, "epa_ramp": 0.5}, {"elo_margin": 1.0, "epa_margin": None, "epa_ramp": 0}]
    nfl.set_base_margins(recs, 0.2)
    assert recs[0]["base_margin"] == pytest.approx(2.0 + 0.2 * 0.5 * 4.0)
    assert recs[1]["base_margin"] == 1.0
