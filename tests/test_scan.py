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


def test_line_rows_compare_only_exact_lines_on_both_sides():
    g = {"kickoff": datetime(2026, 10, 17, 19, 30, tzinfo=timezone.utc), "book": "DraftKings",
         "home": {"location": "Air Force"}, "away": {"location": "UNLV"},
         "home_spread": (-1.5, -110), "away_spread": (1.5, -110),
         "over": (47.5, -110), "under": (47.5, -110)}
    spread = [{"team": "Air Force wins by over 1.5 points", "yes_ask": 0.44, "no_ask": 0.58, "ticker": "S1"},
              {"team": "Air Force wins by over 2.5 points", "yes_ask": 0.30, "no_ask": 0.72, "ticker": "S2"}]
    total = [{"team": "Over 46.5 points", "yes_ask": 0.40, "no_ask": 0.62, "ticker": "T1"}]   # not DK's 47.5
    rows, missed = scan.line_rows(g, "UNLV @ Air Force", spread, total, "espn_1")
    assert missed == 1                                   # the total had no exact Kalshi strike
    yes = next(r for r in rows if r["side"] == "yes")
    assert yes["team"] == "Air Force -1.5" and yes["ticker"] == "S1"
    assert yes["edge"] == pytest.approx(0.5 - 0.44 - 0.07 * 0.44 * 0.56) and yes["flag"]
    no = next(r for r in rows if r["side"] == "no")
    assert no["team"] == "UNLV +1.5" and not no["flag"]


def test_ladder_key_matches_by_team_names_not_ticker_date():
    ladders = {"26OCT17MSSTLSU": [{"team": "LSU wins by over 17.5 points"}, {"team": "Mississippi St. wins by over 1.5 points"}],
               "26OCT17FLATEX": [{"team": "Texas wins by over 8.5 points"}]}
    assert scan.ladder_key(ladders, "LSU", "Mississippi State") == "26OCT17MSSTLSU"
    assert scan.ladder_key(ladders, "Texas", "Florida") is None          # Florida never named
    rematch = {"26SEP05AB": [{"team": "Alpha wins by over 3.5 points"}, {"team": "Beta wins by over 1.5 points"}],
               "26DEC05AB": [{"team": "Alpha wins by over 2.5 points"}, {"team": "Beta wins by over 1.5 points"}]}
    kick = datetime(2026, 12, 5, 20, tzinfo=timezone.utc)
    assert scan.ladder_key(rematch, "Alpha", "Beta", kick) == "26DEC05AB"


def test_kalshi_line_from_ladder():
    lad = [{"team": "LSU wins by over 16.5 points", "yes_ask": 0.55, "yes_bid": 0.54},
           {"team": "LSU wins by over 17.5 points", "yes_ask": 0.50, "yes_bid": 0.49},
           {"team": "LSU wins by over 20.5 points", "yes_ask": 0.43, "yes_bid": 0.42}]
    out = scan.kalshi_lines("LSU", "Mississippi State", lad, [])
    assert out["home_line"] == pytest.approx(-17.4, abs=0.05)


def test_matching_contract_is_the_same_bet():
    lad = [{"home_side": True, "strike": 2.5, "m": {"team": "Duke wins by over 2.5 points", "yes_ask": 0.55, "no_ask": 0.47}},
           {"home_side": True, "strike": 3.5, "m": {"team": "Duke wins by over 3.5 points", "yes_ask": 0.46, "no_ask": 0.56}},
           {"home_side": False, "strike": 1.5, "m": {"team": "UNC wins by over 1.5 points", "yes_ask": 0.30, "no_ask": 0.72}}]
    # Home Duke -3 at the book: home covers if margin > 3. Closest Kalshi strikes are 2.5 and 3.5.
    c = scan.matching_contract(lad, "spread", True, 3.0)
    assert c["side"] == "YES" and c["contract"].startswith("Duke") and c["strike"] in (2.5, 3.5)
    # Betting UNC +3 (home margin below 3): Duke YES is wrong, Duke NO is right.
    c2 = scan.matching_contract(lad, "spread", False, 3.0)
    assert c2["side"] == "NO" and c2["contract"].startswith("Duke")
    tot = [{"strike": 54.5, "m": {"team": "Over 54.5 points", "yes_ask": 0.52, "no_ask": 0.50}}]
    u = scan.matching_contract(tot, "total", False, 55.5)
    assert u["side"] == "NO" and u["line_gain"] == pytest.approx(-1.0)   # Under 54.5 is a worse number than Under 55.5
