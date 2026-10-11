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
