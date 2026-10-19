import pytest

from kalshi import edge_after_fee, kalshi_fee, match_market, should_flag


def test_fee_formula():
    assert kalshi_fee(0.50) == pytest.approx(0.0175)
    assert kalshi_fee(0.10) == pytest.approx(0.07 * 0.1 * 0.9)
    assert kalshi_fee(0.0) == 0
    assert kalshi_fee(1.0) == 0


def test_fee_is_symmetric_and_max_at_half():
    assert kalshi_fee(0.3) == pytest.approx(kalshi_fee(0.7))
    assert kalshi_fee(0.5) > kalshi_fee(0.4) > kalshi_fee(0.2)


def test_fee_rejects_bad_price():
    with pytest.raises(ValueError):
        kalshi_fee(1.2)


def test_edge_after_fee():
    # model 60%, price 55c: fee = 0.07*.55*.45 = 0.017325
    assert edge_after_fee(0.60, 0.55) == pytest.approx(0.60 - 0.55 - 0.017325)


def test_flag_rules():
    assert should_flag(0.035, 0.60, 0.55)          # edge ok, gap 5 pts
    assert not should_flag(0.025, 0.60, 0.55)      # edge too small
    assert not should_flag(0.08, 0.75, 0.60)       # gap 15 pts > 10
    assert should_flag(0.03, 0.60, 0.50)           # exactly at both limits
    assert not should_flag(0.05, 0.60, None)       # no market consensus


def test_match_market_requires_both_teams():
    markets = [
        {"event_ticker": "E1", "team": "Ohio St.", "yes_ask": 0.7},
        {"event_ticker": "E1", "team": "Michigan", "yes_ask": 0.32},
        {"event_ticker": "E2", "team": "Texas", "yes_ask": 0.6},
    ]
    m = match_market(markets, "Ohio State", "Michigan")
    assert m["home"]["yes_ask"] == 0.7 and m["away"]["yes_ask"] == 0.32
    assert match_market(markets, "Texas", "Oklahoma") is None


def test_match_market_handles_kalshi_cfb_spellings():
    from kalshi import match_market
    markets = [{"event_ticker": "E1", "team": "Appalachian St."}, {"event_ticker": "E1", "team": "Louisiana-Monroe"},
               {"event_ticker": "E2", "team": "Miami (FL)"}, {"event_ticker": "E2", "team": "Florida St."}]
    assert match_market(markets, "App State", "UL Monroe")["home"]["team"] == "Appalachian St."
    assert match_market(markets, "Miami", "Florida State")["away"]["team"] == "Florida St."
    assert match_market(markets, "Miami (OH)", "Florida State") is None   # never confuse the two Miamis


def test_cfb_started_games_are_detected():
    from datetime import datetime, timezone
    from week import _started
    now = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)
    assert _started({"start": "2026-10-17T19:30:00.000Z"}, now)
    assert not _started({"start": "2026-10-17T23:30:00.000Z"}, now)
    assert not _started({"start": None}, now)
