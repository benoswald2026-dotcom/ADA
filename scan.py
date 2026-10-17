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
from strategy import LONGSHOT_MIN_PRICE, kelly_fraction_kalshi, normal_cdf, size_bets

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


def ladder_key(spread_by_key, home, away, kickoff=None, keyfn=team_key):
    """The Kalshi game key whose spread contracts name both teams.

    Kalshi's tickers can't be joined across series by key: the same game can be
    KXNCAAFGAME-26OCT16MSSTLSU (winner) but KXNCAAFSPREAD-26OCT17MSSTLSU (spread).
    So match on the team names inside the contracts; if two keys qualify (a rematch),
    take the one dated closest to kickoff."""
    hk, ak = keyfn(home), keyfn(away)
    hits = []
    for k, ms in spread_by_key.items():
        teams = {keyfn(mm.group(1)) for m in ms if (mm := SPREAD_RE.match(m.get("team") or ""))}
        if hk in teams and ak in teams:
            hits.append(k)
    if len(hits) <= 1 or kickoff is None:
        return hits[0] if hits else None

    def days_off(k):
        try:
            return abs((datetime.strptime(k[:7], "%y%b%d").date() - kickoff.date()).days)
        except ValueError:
            return 99
    return min(hits, key=days_off)


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
    spread_by_key = {k: ms for (kind, k), ms in by_game.items() if kind == "spread"}
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
            gk = (ladder_key(spread_by_key, g["home"]["location"], g["away"]["location"], g["kickoff"]) if sport == "cfb"
                  else ladder_key(spread_by_key, g["home"]["displayName"], g["away"]["displayName"], g["kickoff"], T.code_for))
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


def _mid(m):
    """Contract midpoint: (bid + ask) / 2 when both exist, else the ask."""
    a, b = m.get("yes_ask"), m.get("yes_bid")
    if a is None or not 0 < a < 1:
        return None
    return (a + b) / 2 if b is not None and 0 < b < a else a


def _median_from_ladder(points):
    """points: [(x, P(value > x))]. Returns x where the probability crosses 50%
    (linear interpolation), or None if the ladder never crosses it."""
    pts = sorted(points)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return x0 + (p0 - 0.5) * (x1 - x0) / (p0 - p1)
    return None


def kalshi_lines(home, away, spread_mkts, total_mkts, keyfn=team_key):
    """Kalshi's own line for a game, read off its contract ladders.

    Kalshi has no single spread: it lists 'X wins by over s' contracts at many s.
    Each midpoint is P(margin > s); the margin where that crosses 50% is Kalshi's
    line. Same for totals ('Over s'). Returns {"home_line", "total", "spread", "totals"}
    where spread/totals are the parsed contracts (team, strike, home_side, market)."""
    hk, ak = keyfn(home), keyfn(away)
    spread_pts, contracts = [], []
    for m in spread_mkts:
        mm = SPREAD_RE.match(m.get("team") or "")
        p = _mid(m)
        if not mm or p is None:
            continue
        k, strike = keyfn(mm.group(1)), float(mm.group(2))
        if k == hk:
            spread_pts.append((strike, p))
            contracts.append({"home_side": True, "strike": strike, "m": m})
        elif k == ak:
            spread_pts.append((-strike, 1 - p))
            contracts.append({"home_side": False, "strike": strike, "m": m})
    total_pts, totals = [], []
    for m in total_mkts:
        mm = TOTAL_RE.match(m.get("team") or "")
        p = _mid(m)
        if mm and p is not None:
            total_pts.append((float(mm.group(1)), p))
            totals.append({"strike": float(mm.group(1)), "m": m})
    med = _median_from_ladder(spread_pts)
    return {"home_line": None if med is None else -med, "total": _median_from_ladder(total_pts),
            "spread": contracts, "totals": totals}


def matching_contract(contracts, kind, pick_over, line):
    """The Kalshi contract that is the same bet as a pick, at the strike closest to the
    sportsbook line.

    Spread: pick_over=True means 'home margin above the line' (home covers); line is the
    home margin the book needs (e.g. 3.5 for home -3.5, -3.5 for home +3.5). Kalshi's
    'home wins by over s' YES and 'away wins by over s' NO both pay when the home
    margin is high; the mirror images pay when it is low. Total: pick_over=True is Over.
    Returns {"contract", "side", "ask", "strike", "line_gain"} or None. line_gain > 0
    means the Kalshi contract is a better number than the book's line."""
    best = None
    for c in contracts:
        m = c["m"]
        if kind == "spread":
            x = c["strike"] if c["home_side"] else -c["strike"]     # threshold on the home margin
            yes_is_high = c["home_side"]
            name = re.sub(r" points?$", "", m["team"])
        else:
            x, yes_is_high, name = c["strike"], True, f"Over {c['strike']:g}"
        side = "YES" if yes_is_high == pick_over else "NO"
        ask = m.get("yes_ask") if side == "YES" else m.get("no_ask")
        if ask is None or not 0 < ask < 1:
            continue
        gain = (line - x) if pick_over else (x - line)      # better than the book: lower bar when betting high
        cand = {"contract": name, "side": side, "ask": ask, "strike": x, "line_gain": gain, "ticker": m.get("ticker")}
        if best is None or abs(x - line) < abs(best["strike"] - line) or (
                abs(x - line) == abs(best["strike"] - line) and ask < best["ask"]):
            best = cand
    return best
