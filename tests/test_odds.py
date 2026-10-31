import pytest

from odds import american_to_prob, consensus, prob_to_american, remove_vig


def test_american_to_prob():
    assert american_to_prob(-150) == pytest.approx(0.6)
    assert american_to_prob(+150) == pytest.approx(0.4)
    assert american_to_prob(+100) == pytest.approx(0.5)


def test_remove_vig_standard_minus_110_both_sides():
    # -110 / -110: each implies 52.38%, sum 104.76%. Fair = 50/50.
    a, b = remove_vig(-110, -110)
    assert a == pytest.approx(0.5)
    assert b == pytest.approx(0.5)


def test_remove_vig_sums_to_one_and_keeps_ratio():
    a, b = remove_vig(-150, +130)
    raw_a, raw_b = american_to_prob(-150), american_to_prob(+130)
    assert raw_a + raw_b > 1  # vig exists
    assert a + b == pytest.approx(1.0)
    assert a / b == pytest.approx(raw_a / raw_b)
    assert a == pytest.approx(0.6 / (0.6 + 100 / 230))


def test_consensus_averages_books():
    books = [(-110, -110), (-150, +130)]
    expected = (0.5 + remove_vig(-150, +130)[0]) / 2
    assert consensus(books) == pytest.approx(expected)
    assert consensus([]) is None


def test_prob_to_american_round_trip():
    assert prob_to_american(0.6) == pytest.approx(-150)
    assert prob_to_american(0.4) == pytest.approx(150)
    assert american_to_prob(prob_to_american(0.27)) == pytest.approx(0.27)


def _event(*books):
    return {"bookmakers": [{"key": key, "last_update": "2026-10-16T12:00:00Z", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Home", "price": h}, {"name": "Away", "price": a}]}]}
        for key, h, a in books]}


def test_parse_odds_event_prefers_sharp_book_and_keeps_it_out_of_bettable_prices():
    from odds import american_to_decimal, parse_odds_event
    m = parse_odds_event(_event(("pinnacle", -150, +135), ("draftkings", -110, -110)), "Home", "Away")
    assert m["home_prob"] == pytest.approx(remove_vig(-150, +135)[0])
    assert m["prob_source"] == "Pinnacle (sharp)"
    assert m["consensus_prob"] == pytest.approx(0.5)
    assert m["ml_books"] == 1
    assert m["home_ml_dec"] == pytest.approx(american_to_decimal(-110))


def test_parse_odds_event_falls_back_to_book_average_without_sharp_book():
    from odds import parse_odds_event
    m = parse_odds_event(_event(("draftkings", -110, -110), ("fanduel", -150, +130)), "Home", "Away")
    assert m["sharp_prob"] is None
    assert m["home_prob"] == pytest.approx(consensus([(-110, -110), (-150, +130)]))
    assert m["prob_source"] == "book average"
