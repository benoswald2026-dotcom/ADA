from datetime import datetime, timezone

import pytest

import scan


def _event(eid, home, away, h_ml, a_ml, date="2026-10-17T19:30Z", state="pre"):
    team = lambda loc: {"location": loc, "displayName": loc}
    return {"id": eid, "date": date, "competitions": [{
        "status": {"type": {"state": state}},
        "competitors": [{"homeAway": "home", "team": team(home)}, {"homeAway": "away", "team": team(away)}],
        "odds": [{"provider": {"name": "DraftKings"},
                  "moneyline": {"home": {"close": {"odds": h_ml}}, "away": {"close": {"odds": a_ml}}}}]}]}


def test_scan_flags_cheap_kalshi_side_and_skips_started_games(monkeypatch):
    events = [_event("1", "Georgia Tech", "Clemson", "-110", "-110"),            # fair 50/50
              _event("2", "Duke", "UNC", "-110", "-110", date="2026-10-17T15:00Z")]  # already started
    markets = [{"event_ticker": "E1", "team": "Georgia Tech", "yes_ask": 0.44, "yes_bid": 0.43, "ticker": "T1"},
               {"event_ticker": "E1", "team": "Clemson", "yes_ask": 0.57, "yes_bid": 0.56, "ticker": "T2"},
               {"event_ticker": "E2", "team": "Duke", "yes_ask": 0.40, "yes_bid": 0.39, "ticker": "T3"},
               {"event_ticker": "E2", "team": "North Carolina", "yes_ask": 0.61, "yes_bid": 0.60, "ticker": "T4"}]
    monkeypatch.setattr(scan, "kalshi_markets", lambda series: (markets, "k-stamp"))
    monkeypatch.setattr(scan, "espn_events", lambda sport, day: (events if day == "20261017" else [], "e-stamp"))
    now = datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)
    r = scan.scan("cfb", days=1, now=now)
    top = r["rows"][0]
    assert top["team"] == "Georgia Tech" and top["flag"] and top["stake"] > 0
    assert top["edge"] == pytest.approx(0.5 - 0.44 - 0.07 * 0.44 * 0.56)
    assert {x["matchup"] for x in r["rows"]} == {"Clemson @ Georgia Tech"}
    assert any("already kicked off" in n for n in r["notes"])


def test_parse_event_without_odds():
    e = _event("3", "A", "B", "x", "y")
    e["competitions"][0]["odds"] = []
    g = scan.parse_event(e)
    assert g["home_ml"] is None and g["away_ml"] is None
