import pytest

from parlay import expected_value, fair_parlay_prob, fair_price, offered_decimal, same_game_warning


def test_two_leg_product():
    assert fair_parlay_prob([0.6, 0.7]) == pytest.approx(0.42)


def test_three_leg_product():
    assert fair_parlay_prob([0.5, 0.5, 0.5]) == pytest.approx(0.125)


def test_fair_price_odds():
    p, american, decimal = fair_price([0.5, 0.5])
    assert p == pytest.approx(0.25)
    assert american == pytest.approx(300)   # +300 = 3:1 for a 25% event
    assert decimal == pytest.approx(4.0)


def test_leg_count_limits():
    with pytest.raises(ValueError):
        fair_parlay_prob([0.5])
    with pytest.raises(ValueError):
        fair_parlay_prob([0.5] * 4)


def test_offered_and_ev():
    d = offered_decimal([1.909, 1.909])      # two -110 legs
    assert d == pytest.approx(3.644, abs=1e-3)
    assert expected_value(0.25, d) == pytest.approx(0.25 * d - 1)


def test_same_game_warning():
    assert same_game_warning([1, 2]) is None
    assert "correlated" in same_game_warning([1, 1, 2])
