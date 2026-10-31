"""Closing line value, line shopping and Kalshi limit orders.

What documented winning bettors measure and do (Benter, Walters, Kaunitz et al.,
Burgi-Deng-Whelan on Kalshi): beat the CLOSING price, shop many books against a
sharp book's no-vig price, and on exchanges post orders (maker) instead of paying
the taker fee.

  record_lines()  every Kalshi + DraftKings price now -> data/line_history.csv
  picks_clv()     each graded consensus pick: our line vs the closing line, in points
  shop()          books whose price beats Pinnacle's no-vig price (needs ODDS_API_KEY)
  maker_quotes()  where to post a Kalshi bid so a fill is +EV after the maker fee
Read-only: nothing here places an order.
"""
import csv
import os
from datetime import datetime, timezone

import config
from cache import ApiError

HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "line_history.csv")
HISTORY_FIELDS = ["at", "sport", "game_id", "matchup", "kickoff", "market_type", "bet", "side", "ticker",
                  "book_line", "book_fair", "kalshi_bid", "kalshi_ask"]
# Fixed before use. Shopping: a book must beat Pinnacle's no-vig price by 2% EV.
# Maker: a resting bid must be +1.5 pts after the maker fee, never below 15c (Kalshi
# longshots under 10-15c lose most of their price on average: Burgi, Deng & Whelan).
SHOP_MIN_EV = 0.02
MAKER_MIN_EDGE = 0.015
MAKER_MIN_PRICE = 0.15
# Kalshi's maker fee is a quarter of the taker fee (0.0175 vs 0.07 x P x (1-P)) where it
# applies; some series charge makers nothing. The larger figure is used to stay safe.
MAKER_FEE_RATE = 0.0175


def maker_fee(price):
    return MAKER_FEE_RATE * price * (1 - price)


# ------------------------------------------------------------ line history --

def record_lines(sport="nfl", days=1, path=None):
    """Append every current Kalshi contract that sits on DraftKings' line (plus winners),
    with DraftKings' no-vig probability, to the line history. Returns rows written."""
    from cache import fresh
    from scan import scan
    path = path or HISTORY
    with fresh():
        r = scan(sport, days=days)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"at": now, "sport": sport, "game_id": x["game_id"], "matchup": x["matchup"],
             "kickoff": x["kickoff"].isoformat(), "market_type": x["market_type"], "bet": x["team"],
             "side": x.get("side", "yes"), "ticker": x.get("ticker"), "book_line": x.get("book_line", ""),
             "book_fair": round(x["market"], 4), "kalshi_bid": x.get("bid"), "kalshi_ask": x["price"]}
            for x in r["rows"]]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


def price_moves(path=None):
    """Per contract: first and last recorded Kalshi ask before kickoff (open vs close)."""
    path = path or HISTORY
    if not os.path.exists(path):
        return []
    first, last = {}, {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["at"] >= r["kickoff"][:19].replace("+00:00", "") + "Z":
                continue
            key = (r["game_id"], r["ticker"], r["side"])
            first.setdefault(key, r)
            last[key] = r
    return [{"matchup": first[k]["matchup"], "bet": first[k]["bet"], "open": float(first[k]["kalshi_ask"]),
             "close": float(last[k]["kalshi_ask"]), "move": float(last[k]["kalshi_ask"]) - float(first[k]["kalshi_ask"]),
             "snapshots": (first[k]["at"], last[k]["at"])} for k in first]


# -------------------------------------------------------------- picks CLV --

def closing_line(sport, event_id):
    """(home spread, total) DraftKings closed at, from ESPN's game summary, or None before
    the game ends. The scoreboard drops odds after a game; the summary keeps them. Finished
    games are cached for good."""
    from cache import cached_get
    lg = "nfl" if sport == "nfl" else "college-football"
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/{lg}/summary"
    try:
        d, _ = cached_get(url, params={"event": event_id}, ttl=3600, source="ESPN summary")
        if (d.get("header", {}).get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("state") != "post":
            return None
        d, _ = cached_get(url, params={"event": event_id, "final": 1}, ttl=None, source="ESPN summary")
    except ApiError:
        return None
    pc = (d.get("pickcenter") or [{}])[0]
    spread, total = pc.get("spread"), pc.get("overUnder")
    if spread is None and total is None:
        return None
    return (None if spread is None else float(spread), None if total is None else float(total))


def pick_clv(pick, close_home_line, close_total):
    """Points of closing line value for one logged pick (positive = we beat the close).
    Spread lines are the home team's (-5.5 = home favored by 5.5)."""
    line = float(pick["line"])
    if pick["bet_type"] == "Spread":
        if close_home_line is None:
            return None
        took_home = pick["pick"] == f"{pick['home']} {line:+g}"
        # Home at -5.5 that closes -7: we got 1.5 points better. Away is the mirror image.
        return (line - close_home_line) if took_home else (close_home_line - line)
    if close_total is None:
        return None
    return (close_total - line) if pick["pick"].startswith("Over") else (line - close_total)


def picks_clv(path=None):
    """CLV for every logged consensus pick whose game has a closing line on ESPN."""
    import consensus as C
    path = path or C.PICKS_FILE
    if not os.path.exists(path):
        return {"rows": [], "avg": None, "beat": None}
    with open(path, newline="") as f:
        picks = list(csv.DictReader(f))
    closes = {}
    for sport, eid in sorted({(p["sport"], p["event_id"]) for p in picks}):
        c = closing_line(sport, eid)
        if c:
            closes[eid] = c
    rows = []
    for p in picks:
        if p["event_id"] not in closes:
            continue
        clv = pick_clv(p, *closes[p["event_id"]])
        if clv is not None:
            rows.append({**p, "close": closes[p["event_id"]], "clv": clv})
    vals = [r["clv"] for r in rows]
    return {"rows": rows, "avg": sum(vals) / len(vals) if vals else None,
            "beat": sum(v > 0 for v in vals) / len(vals) if vals else None,
            "same": sum(v == 0 for v in vals) / len(vals) if vals else None}


# --------------------------------------------------------------- shopping --

def best_prices(event, sharp="pinnacle"):
    """One Odds API event -> bets where a book beats the sharp book's no-vig price.
    Only the same market at the same point is compared (a -3 vs a -3.5 is not the same bet)."""
    from odds import american_to_decimal, remove_vig
    books = {b["key"]: b for b in event.get("bookmakers", [])}
    if sharp not in books:
        return []
    out = []
    for mk in books[sharp].get("markets", []):
        outs = mk.get("outcomes", [])
        if len(outs) != 2:
            continue
        fair = dict(zip([(o["name"], o.get("point")) for o in outs], remove_vig(outs[0]["price"], outs[1]["price"])))
        for key, b in books.items():
            if key == sharp:
                continue
            for m2 in b.get("markets", []):
                if m2["key"] != mk["key"]:
                    continue
                for o in m2.get("outcomes", []):
                    p = fair.get((o["name"], o.get("point")))
                    if p is None:
                        continue
                    ev = p * american_to_decimal(o["price"]) - 1
                    out.append({"matchup": f"{event['away_team']} @ {event['home_team']}", "kickoff": event.get("commence_time"),
                                "market": mk["key"], "bet": o["name"] + (f" {o['point']:+g}" if mk["key"] == "spreads" else
                                                                       f" {o['point']:g}" if o.get("point") is not None else ""),
                                "book": key, "price": o["price"], "fair": p, "ev": ev, "flag": ev >= SHOP_MIN_EV})
    return out


def shop(sport="nfl"):
    """All bets at every book vs Pinnacle's no-vig price, best EV first. Needs ODDS_API_KEY."""
    from cache import cached_get
    league = "americanfootball_nfl" if sport == "nfl" else "americanfootball_ncaaf"
    params = {"apiKey": config.get_key("ODDS_API_KEY"), "bookmakers": ",".join(config.NFL_ODDS_BOOKS),
              "markets": "h2h,spreads,totals", "oddsFormat": "american", "dateFormat": "iso"}
    events, stamp = cached_get(f"https://api.the-odds-api.com/v4/sports/{league}/odds", params=params,
                               ttl=config.TTL_ODDS, source="The Odds API")
    rows = [r for e in events for r in best_prices(e)]
    rows.sort(key=lambda r: -r["ev"])
    return rows, stamp


# ------------------------------------------------------------ maker quotes --

def power_devig(odds_a, odds_b):
    """No-vig probabilities by the power method: find k with q_a^k + q_b^k = 1. Unlike the
    proportional method, it takes more of the vig out of the longshot, which is where
    bettors overpay (the favorite-longshot bias). Returns (p_a, p_b)."""
    from odds import american_to_prob
    qa, qb = american_to_prob(odds_a), american_to_prob(odds_b)
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if qa ** k + qb ** k > 1 else (lo, k)
    k = (lo + hi) / 2
    return qa ** k, qb ** k


def best_bid(fair, bid, ask, min_edge=MAKER_MIN_EDGE):
    """Highest whole-cent price that improves the current bid, stays under the ask, and is
    still +min_edge after the maker fee if filled. None when no such price exists."""
    lo = int(round((bid or 0) * 100))       # joining the current bid is allowed (just queued behind it)
    hi = int(round(ask * 100)) - 1
    for c in range(hi, lo - 1, -1):
        x = c / 100
        if x >= MAKER_MIN_PRICE and fair - x - maker_fee(x) >= min_edge:
            return x
    return None


def maker_quotes(sport="nfl", days=1):
    """Kalshi contracts where posting a bid (instead of paying the ask) is +EV if filled,
    using DraftKings' no-vig probability as fair value."""
    from cache import fresh
    from scan import scan
    with fresh():
        r = scan(sport, days=days)
    # Winners: re-price DraftKings' two moneylines with the power method (see power_devig).
    ml = {}
    for x in r["rows"]:
        if x["market_type"] == "moneyline" and x.get("book_ml") is not None:
            ml.setdefault(x["game_id"], {})[x["side"]] = x["book_ml"]
    out = []
    for x in r["rows"]:
        ask, fair = x["price"], x["market"]
        pair = ml.get(x["game_id"], {})
        if x["market_type"] == "moneyline" and len(pair) == 2:
            other = "away" if x["side"] == "home" else "home"
            fair = power_devig(pair[x["side"]], pair[other])[0]
        if x.get("side") == "no":
            continue   # NO bids aren't in the feed; a NO bid is the same as a YES offer anyway
        bid = x.get("bid")
        post = best_bid(fair, bid, ask)
        if post is None:
            continue
        out.append({**x, "fair": fair, "post": post, "edge_if_filled": fair - post - maker_fee(post),
                    "taker_edge": x["edge"]})
    out.sort(key=lambda q: -q["edge_if_filled"])
    return out, r
