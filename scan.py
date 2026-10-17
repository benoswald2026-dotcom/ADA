"""Kalshi vs. sportsbook scan: needs no API keys.

For every upcoming game, compare Kalshi's prices with the DraftKings
moneyline, spread and total that ESPN's public scoreboard shows, after
removing DraftKings' vig. Spreads and totals are compared only where Kalshi
has a contract at exactly DraftKings' line (both sides: YES and NO), so no
assumed margin distribution can manufacture an edge.
The bet probability is that market number, not the model: the NFL backtest
fitted the model's trust weight at 0, so the defensible edge is Kalshi
disagreeing with the sportsbooks. Same rules as `bets`: FLAG = edge after
Kalshi's fee >= 3 points and price >= 20 cents; quarter-Kelly stakes with the
usual caps. Games already under way are skipped.

Caveat: DraftKings is one recreational book. A sharp book (Pinnacle, via
ODDS_API_KEY and `bets`) is a better reference when you have it.
Read-only: nothing here places a bet.
"""
import re
from datetime import datetime, timedelta, timezone

import config
import nfl_teams as T
from api_clients import kalshi_markets
from cache import cached_get
from kalshi import edge_after_fee, kalshi_fee, match_market
from names import team_key
from odds import remove_vig
from strategy import LONGSHOT_MIN_PRICE, kelly_fraction_kalshi, size_bets

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/{league}/scoreboard"
SPORTS = {
    "cfb": {"league": "college-football", "series": "KXNCAAFGAME", "spread": "KXNCAAFSPREAD",
            "total": "KXNCAAFTOTAL", "params": {"groups": 80, "limit": 300}},
    "nfl": {"league": "nfl", "series": "KXNFLGAME", "spread": "KXNFLSPREAD", "total": "KXNFLTOTAL", "params": {}},
}
SPREAD_RE = re.compile(r"^(.*) wins by over ([\d.]+) points?$", re.I)
TOTAL_RE = re.compile(r"^over ([\d.]+) points?", re.I)


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

    def line_odds(block, side, strip=""):
        try:
            close = block[side]["close"]
            return float(str(close["line"]).lstrip(strip).replace("+", "")), int(str(close["odds"]).replace("+", ""))
        except (KeyError, TypeError, ValueError):
            return None

    o = (c.get("odds") or [{}])[0]
    return {"id": e.get("id"), "kickoff": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
            "state": c["status"]["type"]["state"],
            "home": teams["home"], "away": teams["away"],
            "home_ml": price("home"), "away_ml": price("away"),
            "home_spread": line_odds(o.get("pointSpread") or {}, "home"),
            "away_spread": line_odds(o.get("pointSpread") or {}, "away"),
            "over": line_odds(o.get("total") or {}, "over", "o"),
            "under": line_odds(o.get("total") or {}, "under", "u"),
            "book": ((c.get("odds") or [{}])[0].get("provider") or {}).get("name", "sportsbook")}


def _match(sport, markets, g):
    if sport == "cfb":
        return match_market(markets, g["home"]["location"], g["away"]["location"])
    from nfl import _match_kalshi
    h, a = T.code_for(g["home"]["displayName"]), T.code_for(g["away"]["displayName"])
    return _match_kalshi(markets, h, a) if h and a else None


def _game_key(event_ticker):
    """'KXNCAAFSPREAD-26OCT17OSUIND' -> '26OCT17OSUIND': the same game across Kalshi series."""
    return event_ticker.split("-", 1)[1] if "-" in event_ticker else event_ticker


def _side_rows(base, p_yes, m, label):
    """Rows for buying YES and buying NO on one Kalshi contract whose fair YES probability is p_yes."""
    out = []
    for side, p, ask, name in (("yes", p_yes, m.get("yes_ask"), label["yes"]), ("no", 1 - p_yes, m.get("no_ask"), label["no"])):
        if ask is None or not 0 < ask < 1:
            continue
        edge = edge_after_fee(p, ask)
        out.append({**base, "team": name, "side": side, "model": p, "market": p, "price": ask,
                    "bid": m.get("yes_bid") if side == "yes" else None, "fee": kalshi_fee(ask), "edge": edge,
                    "ev": edge / (ask + kalshi_fee(ask)), "decimal": 1 / (ask + kalshi_fee(ask)),
                    "ticker": m.get("ticker"), "longshot": ask < LONGSHOT_MIN_PRICE,
                    "flag": edge >= config.MIN_EDGE and ask >= LONGSHOT_MIN_PRICE,
                    "kelly": kelly_fraction_kalshi(p, ask)})
    return out


def line_rows(g, name, spread_mkts, total_mkts, game_id):
    """Spread and total rows where a Kalshi contract sits exactly on DraftKings' line.
    Returns (rows, how many DraftKings lines had no exact Kalshi contract)."""
    rows, missed = [], 0
    base = {"game_id": game_id, "matchup": name, "kickoff": g["kickoff"], "venue": "kalshi", "book": g["book"]}
    # Spread: Kalshi "X wins by over s" matches DraftKings' line for X of -s.
    if g["home_spread"] and g["away_spread"]:
        (h_line, h_odds), (a_line, a_odds) = g["home_spread"], g["away_spread"]
        p_home_cover = remove_vig(h_odds, a_odds)[0]
        fav, fav_line, p_fav = ((g["home"], h_line, p_home_cover) if h_line < 0 else (g["away"], a_line, 1 - p_home_cover))
        dog = g["away"] if fav is g["home"] else g["home"]
        hit = [m for m in spread_mkts if (mm := SPREAD_RE.match(m["team"] or "")) and
               team_key(mm.group(1)) == team_key(fav["location"]) and abs(float(mm.group(2)) + fav_line) < 1e-9]
        if hit:
            s_ = -fav_line
            rows += _side_rows({**base, "market_type": "spread", "book_line": fav_line}, p_fav, hit[0],
                               {"yes": f"{fav['location']} -{s_:g}", "no": f"{dog['location']} +{s_:g}"})
        elif fav_line != 0:
            missed += 1
    # Total: Kalshi "Over s" matches DraftKings' total s.
    if g["over"] and g["under"]:
        (o_line, o_odds), (_, u_odds) = g["over"], g["under"]
        p_over = remove_vig(o_odds, u_odds)[0]
        hit = [m for m in total_mkts if (mm := TOTAL_RE.match(m["team"] or "")) and abs(float(mm.group(1)) - o_line) < 1e-9]
        if hit:
            rows += _side_rows({**base, "market_type": "total", "book_line": o_line}, p_over, hit[0],
                               {"yes": f"Over {o_line:g}", "no": f"Under {o_line:g}"})
        else:
            missed += 1
    return rows, missed


def scan(sport="cfb", days=1, now=None, bankroll=1000.0):
    """Returns {"rows": [...sides sorted by edge], "notes": [...], "kalshi_stamp", "espn_stamp"}."""
    now = now or datetime.now(timezone.utc)
    markets, kstamp = kalshi_markets(SPORTS[sport]["series"])
    by_game = {}
    for kind in ("spread", "total"):
        try:
            ms, _ = kalshi_markets(SPORTS[sport][kind])
        except Exception:   # a missing ladder shouldn't sink the moneyline scan
            ms = []
        for m in ms:
            by_game.setdefault((kind, _game_key(m["event_ticker"])), []).append(m)
    rows, notes, started, nomatch, noodds, estamp = [], [], [], [], [], None
    missed_lines = 0
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
            gk = _game_key(km["home"]["event_ticker"])
            lr, missed = line_rows(g, name, by_game.get(("spread", gk), []), by_game.get(("total", gk), []),
                                   f"espn_{g['id']}")
            rows += lr
            missed_lines += missed
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
    if missed_lines:
        notes.append(f"{missed_lines} DraftKings spread/total lines had no Kalshi contract at exactly that line "
                     "(e.g. a whole-number spread); those are skipped rather than estimated.")
    if noodds:
        notes.append(f"No sportsbook odds on ESPN for {len(noodds)} games: " + "; ".join(noodds))
    return {"rows": rows, "notes": notes, "kalshi_stamp": kstamp, "espn_stamp": estamp,
            "games": len({r["matchup"] for r in rows})}
