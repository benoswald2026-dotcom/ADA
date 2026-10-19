import pytest

import lines


def test_pick_clv_spreads_and_totals():
    home = {"bet_type": "Spread", "pick": "Maryland -5.5", "home": "Maryland", "line": "-5.5"}
    assert lines.pick_clv(home, -7.0, None) == pytest.approx(1.5)      # closed -7: we beat it
    assert lines.pick_clv(home, -4.5, None) == pytest.approx(-1.0)
    away = {"bet_type": "Spread", "pick": "Elon +34.5", "home": "Stanford", "line": "-34.5"}
    assert lines.pick_clv(away, -33.5, None) == pytest.approx(1.0)      # Elon closed +33.5
    over = {"bet_type": "Total", "pick": "Over 45.5", "home": "X", "line": "45.5"}
    assert lines.pick_clv(over, None, 47.0) == pytest.approx(1.5)
    assert lines.pick_clv({**over, "pick": "Under 45.5"}, None, 47.0) == pytest.approx(-1.5)
    assert lines.pick_clv(home, None, 47.0) is None


def test_power_devig_takes_more_from_the_longshot():
    from odds import remove_vig
    p_fav, p_dog = lines.power_devig(-400, 300)
    assert p_fav + p_dog == pytest.approx(1.0)
    assert p_dog < remove_vig(300, -400)[0]       # proportional gives the dog more


def test_best_bid_rules():
    # Fair 52%: posting at 49c is +1.5 after a 0.44c maker fee; it stays under the ask.
    assert lines.best_bid(0.52, 0.48, 0.52) == pytest.approx(0.50)
    assert lines.best_bid(0.50, 0.49, 0.50) is None          # no room for +1.5 pts
    assert lines.best_bid(0.16, 0.10, 0.14) is None          # longshots (< 15c) are never quoted


def test_best_prices_compares_same_point_only():
    ev = {"away_team": "A", "home_team": "B", "commence_time": "t", "bookmakers": [
        {"key": "pinnacle", "markets": [{"key": "spreads", "outcomes": [
            {"name": "A", "price": -105, "point": 3.5}, {"name": "B", "price": -105, "point": -3.5}]}]},
        {"key": "fanduel", "markets": [{"key": "spreads", "outcomes": [
            {"name": "A", "price": 110, "point": 3.5}, {"name": "B", "price": -130, "point": -3.0}]}]}]}
    rows = lines.best_prices(ev)
    assert [r["bet"] for r in rows] == ["A +3.5"]             # B -3 isn't the same bet as B -3.5
    assert rows[0]["fair"] == pytest.approx(0.5) and rows[0]["ev"] == pytest.approx(0.05) and rows[0]["flag"]
