"""Kalshi vs. sportsbook scan: needs no API keys.

For every upcoming game, compare Kalshi's YES ask with the DraftKings
moneyline that ESPN's public scoreboard shows, after removing DraftKings' vig.
The bet probability is that market number, not the model: the NFL backtest
fitted the model's trust weight at 0, so the defensible edge is Kalshi
disagreeing with the sportsbooks. Same rules as `bets`: FLAG = edge after
Kalshi's fee >= 3 points and price >= 20 cents; quarter-Kelly stakes with the
usual caps. Games already under way are skipped.

Caveat: DraftKings is one recreational book. A sharp book (Pinnacle, via
ODDS_API_KEY and `bets`) is a better reference when you have it.
Read-only: nothing here places a bet.
"""
from datetime import datetime, timedelta, timezone

import config
import nfl_teams as T
from api_clients import kalshi_markets
from cache import cached_get
from kalshi import edge_after_fee, kalshi_fee, match_market
from odds import remove_vig
from strategy import LONGSHOT_MIN_PRICE, kelly_fraction_kalshi, size_bets

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/{league}/scoreboard"
SPORTS = {
    "cfb": {"league": "college-football", "series": "KXNCAAFGAME", "params": {"groups": 80, "limit": 300}},
    "nfl": {"league": "nfl", "series": "KXNFLGAME", "params": {}},
}


def espn_events(sport, day):
    """ESPN scoreboard events for one date (YYYYMMDD). Cached like the odds (30 min)."""
    s = SPORTS[sport]
    data, stamp = cached_get(ESPN.format(league=s["league"]), params={"dates": day, **s["params"]},
                             ttl=config.TTL_ODDS, source="ESPN scoreboard")
    return data.get("events", []), stamp


def parse_event(e):
    """One ESPN event -> dict with kickoff, state, home/away names and DraftKings
    moneylines (None when ESPN shows no odds)."""
    c = e["competitions"][0]
    teams = {t["homeAway"]: t["team"] for t in c["competitors"]}
    ml = ((c.get("odds") or [{}])[0].get("moneyline") or {})

    def price(side):
        try:
            return int(str(ml[side]["close"]["odds"]).replace("+", ""))
        except (KeyError, TypeError, ValueError):
            return None

    return {"id": e.get("id"), "kickoff": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
            "state": c["status"]["type"]["state"],
            "home": teams["home"], "away": teams["away"],
            "home_ml": price("home"), "away_ml": price("away"),
            "book": ((c.get("odds") or [{}])[0].get("provider") or {}).get("name", "sportsbook")}


def _match(sport, markets, g):
    if sport == "cfb":
        return match_market(markets, g["home"]["location"], g["away"]["location"])
    from nfl import _match_kalshi
    h, a = T.code_for(g["home"]["displayName"]), T.code_for(g["away"]["displayName"])
    return _match_kalshi(markets, h, a) if h and a else None


def scan(sport="cfb", days=1, now=None, bankroll=1000.0):
    """Returns {"rows": [...sides sorted by edge], "notes": [...], "kalshi_stamp", "espn_stamp"}."""
    now = now or datetime.now(timezone.utc)
    markets, kstamp = kalshi_markets(SPORTS[sport]["series"])
    rows, notes, started, nomatch, noodds, estamp = [], [], [], [], [], None
    for d in range(days + 1):
        events, estamp = espn_events(sport, (now + timedelta(days=d)).strftime("%Y%m%d"))
        for e in events:
            g = parse_event(e)
            name = f"{g['away']['location']} @ {g['home']['location']}"
            if g["state"] != "pre" or g["kickoff"] <= now:
                if g["state"] == "in" or (g["state"] == "pre" and g["kickoff"] <= now):
                    started.append(name)
                continue
            km = _match(sport, markets, g)
            if not km:
                nomatch.append(name)
                continue
            if g["home_ml"] is None or g["away_ml"] is None:
                noodds.append(name)
                continue
            p_home, p_away = remove_vig(g["home_ml"], g["away_ml"])
            for side, p, ml in (("home", p_home, g["home_ml"]), ("away", p_away, g["away_ml"])):
                k = km[side]
                ask = k["yes_ask"]
                if ask is None or not 0 < ask < 1:
                    continue
                edge = edge_after_fee(p, ask)
                rows.append({"game_id": f"espn_{g['id']}", "matchup": name, "kickoff": g["kickoff"],
                             "team": g[side]["location"], "side": side, "book_ml": ml, "book": g["book"],
                             "model": p, "market": p, "price": ask, "bid": k.get("yes_bid"),
                             "fee": kalshi_fee(ask), "edge": edge, "ev": edge / (ask + kalshi_fee(ask)),
                             "decimal": 1 / (ask + kalshi_fee(ask)), "venue": "kalshi",
                             "market_type": "moneyline", "ticker": k.get("ticker"),
                             "longshot": ask < LONGSHOT_MIN_PRICE,
                             "flag": edge >= config.MIN_EDGE and ask >= LONGSHOT_MIN_PRICE,
                             "kelly": kelly_fraction_kalshi(p, ask)})
    flagged = [r for r in rows if r["flag"]]
    size_bets(flagged, bankroll)
    for r in rows:
        r.setdefault("stake", 0.0)
    rows.sort(key=lambda r: r["edge"], reverse=True)
    if started:
        notes.append("Skipped (already kicked off): " + "; ".join(started))
    if nomatch:
        notes.append(f"No Kalshi market for {len(nomatch)} games: " + "; ".join(nomatch))
    if noodds:
        notes.append(f"No sportsbook odds on ESPN for {len(noodds)} games: " + "; ".join(noodds))
    return {"rows": rows, "notes": notes, "kalshi_stamp": kstamp, "espn_stamp": estamp,
            "games": len({r["matchup"] for r in rows})}
