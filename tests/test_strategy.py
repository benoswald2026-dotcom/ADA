import numpy as np
import pytest

import strategy as S
from kalshi import kalshi_fee, should_flag


def test_kelly_decimal_formula():
    # 55% at even money (2.0): f = (0.55*2 - 1)/(2 - 1) = 0.10
    assert S.kelly_fraction_decimal(0.55, 2.0) == pytest.approx(0.10)
    assert S.kelly_fraction_decimal(0.45, 2.0) == 0.0          # no edge -> no bet


def test_kelly_kalshi_includes_fee():
    p, price = 0.60, 0.50
    c = price + kalshi_fee(price)
    assert S.kelly_fraction_kalshi(p, price) == pytest.approx((p - c) / (1 - c))


def test_size_bets_applies_caps():
    bets = [{"game_id": i, "kelly": 0.5} for i in range(10)]          # huge edges
    S.size_bets(bets, bankroll=1000)
    assert all(b["stake_fraction"] <= S.MAX_BET_FRACTION + 1e-12 for b in bets)
    assert sum(b["stake_fraction"] for b in bets) <= S.MAX_SLATE_FRACTION + 1e-12
    same_game = [{"game_id": 1, "kelly": 0.5}, {"game_id": 1, "kelly": 0.5}]
    S.size_bets(same_game, bankroll=1000)
    assert sum(b["stake_fraction"] for b in same_game) <= S.MAX_GAME_FRACTION + 1e-12


def test_cover_prob_is_half_at_the_line():
    assert S.cover_prob(3.0, -3.0, 13) == pytest.approx(0.5)   # predicted margin equals the spread
    assert S.cover_prob(10.0, -3.0, 13) > 0.5


def test_longshot_guard_blocks_cheap_kalshi_contracts():
    assert should_flag(0.10, 0.25, 0.25, price=0.12) is False   # big edge but a 12c long shot
    assert should_flag(0.05, 0.60, 0.58, price=0.52) is True


def test_bootstrap_and_t_stat():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0, 1.0, 500)
    lo, hi = S.bootstrap_ci(r)
    assert lo < 0 < hi
    assert abs(S.t_stat(r)) < 3


def test_max_drawdown():
    assert S.max_drawdown([1.0, 1.2, 0.9, 1.3]) == pytest.approx(0.25)


def test_fresh_mode_bypasses_ttl_but_keeps_history():
    import cache
    assert cache._still_valid(300, 10) is True          # recent live data reused normally
    with cache.fresh():
        assert cache._still_valid(300, 10) is False     # Refresh: re-download live data
        assert cache._still_valid(None, 10**9) is True  # finished seasons never re-download
    assert cache._still_valid(300, 10) is True


def test_paired_brier_diff_sign_and_ci():
    from backtest import paired_brier_diff
    y = [1, 0] * 200
    good = [0.8, 0.2] * 200
    bad = [0.6, 0.4] * 200
    d = paired_brier_diff(good, bad, y)
    assert d["diff"] == pytest.approx(0.04 - 0.16)
    assert d["ci95"][1] < 0


def test_margin_dist_sums_to_one_and_key_weight_moves_mass_to_3():
    ks, p = S.margin_dist(-2.5, 13.0)
    assert p.sum() == pytest.approx(1.0)
    _, pk = S.margin_dist(-2.5, 13.0, {3: 2.5, -3: 2.5})
    assert pk[ks == -3][0] > 2 * p[ks == -3][0]
    assert pk.sum() == pytest.approx(1.0)


def test_cover_push_prob_whole_and_half_point_lines():
    w, push = S.cover_push_prob(3.0, -3.0, 13.0, {3: 2.6, -3: 2.6})
    assert push > 0.06                       # landing exactly on 3 is common (normal curve alone: ~3%)
    assert w + push < 1
    w_half, push_half = S.cover_push_prob(3.0, -2.5, 13.0, {3: 2.6, -3: 2.6})
    assert push_half == 0.0
    assert w_half == pytest.approx(w + push)  # -2.5 wins whenever -3 wins or pushes
    # with no key weights it matches the old continuous normal closely at a half-point line
    assert S.cover_push_prob(3.0, -2.5, 13.0)[0] == pytest.approx(S.cover_prob(3.0, -2.5, 13.0), abs=0.002)


def test_kelly_with_push_matches_plain_kelly_when_no_push():
    assert S.kelly_with_push(0.55, 0.0, 1.91) == pytest.approx(S.kelly_fraction_decimal(0.55, 1.91))
    assert S.kelly_with_push(0.50, 0.10, 1.91) > S.kelly_fraction_decimal(0.50, 1.91)


def test_key_weights_keep_the_line_a_coin_flip():
    w = {k: v for k, v in {0: 0.06, 1: 0.8, 3: 2.64, 7: 1.72, 10: 1.1, 14: 1.5}.items()}
    w.update({-k: v for k, v in w.items()})
    for mu in (2.5, 3.0, 7.5, -10.5):
        ks, p = S.margin_dist(mu, 12.8, w)
        assert p[ks > mu + 1e-9].sum() == pytest.approx(p[ks < mu - 1e-9].sum(), abs=1e-6)
    # crossing the 3 is worth more than crossing the 5
    ks, p = S.margin_dist(3.0, 12.8, w)
    assert p[ks == 3][0] > 2 * p[ks == 5][0]
