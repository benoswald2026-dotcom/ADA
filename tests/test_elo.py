import pytest

from ratings import RatingSystem, elo_update, expected_score, margin_to_prob, opponent_adjusted_epa


def test_expected_score():
    assert expected_score(1500, 1500) == pytest.approx(0.5)
    assert expected_score(1600, 1500) == pytest.approx(1 / (1 + 10 ** (-0.25)))
    assert expected_score(1600, 1500) + expected_score(1500, 1600) == pytest.approx(1)


def test_update_is_zero_sum_and_winner_gains():
    h, a = elo_update(1500, 1500, 28, 21)
    assert h > 1500 > a
    assert h + a == pytest.approx(3000)


def test_update_known_value():
    # Equal teams, 7-pt home win, no home field: change = 25 * ln(8) * 1 * 0.5
    import math
    h, _ = elo_update(1500, 1500, 28, 21, k=25)
    assert h - 1500 == pytest.approx(25 * math.log(8) * 0.5)


def test_upset_moves_more_than_expected_win():
    fav_win, _ = elo_update(1700, 1400, 24, 21)
    _, dog_win = elo_update(1700, 1400, 21, 24)
    assert (fav_win - 1700) < (dog_win - 1400)


def test_home_field_reduces_home_gain():
    no_hfa, _ = elo_update(1500, 1500, 24, 21, home_field_elo=0)
    hfa, _ = elo_update(1500, 1500, 24, 21, home_field_elo=62.5)
    assert hfa < no_hfa


def test_margin_to_prob():
    assert margin_to_prob(0) == pytest.approx(0.5)
    assert margin_to_prob(7) > 0.7
    assert margin_to_prob(-7) == pytest.approx(1 - margin_to_prob(7))


def test_season_regression():
    rs = RatingSystem()
    rs.elo["A"], rs.is_fbs["A"] = 1650, True
    rs.start_season()
    assert rs.elo["A"] == pytest.approx(1600)


def test_opponent_adjusted_epa_rewards_tougher_schedule():
    # A and B both post +0.1 offense, but A did it vs a stingy defense (C).
    rows = [
        {"team": "A", "opponent": "C", "off_epa": 0.1, "def_epa": 0.0},
        {"team": "C", "opponent": "A", "off_epa": 0.0, "def_epa": 0.1},
        {"team": "C", "opponent": "D", "off_epa": 0.0, "def_epa": -0.3},
        {"team": "D", "opponent": "C", "off_epa": -0.3, "def_epa": 0.0},
        {"team": "B", "opponent": "E", "off_epa": 0.1, "def_epa": 0.0},
        {"team": "E", "opponent": "B", "off_epa": 0.0, "def_epa": 0.1},
    ]
    adj = opponent_adjusted_epa(rows)
    assert adj["A"][0] > adj["B"][0]
