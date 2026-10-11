"""Bet finder: ranks Kalshi singles, sportsbook singles, and 2-3 leg parlays
from a slate of rows (NFL or college). Read-only: it only suggests.

Every candidate must pass the project's safety rule: the model and the
market consensus must be within 10 points. A bigger gap usually means the
model is missing information, not that the market is wrong.

"Likely to win" is not the goal. A bet only makes money over time when the
price is better than the true chance, so everything here is ranked by
expected value (EV), and the win chance is shown next to it.
"""
from itertools import combinations

import config
from kalshi import edge_after_fee, kalshi_fee


def _sides(row):
    """Yield (side, team, bet prob, market prob, avg decimal odds, raw model prob) for both teams.
    bet prob = what edges use (NFL: market-blended); raw model = what the 10-pt rule checks."""
    g, p, m = row["game"], row["pred"], row["market"]
    raw_home = p.get("raw_home_prob", p["home_prob"])
    for side in ("home", "away"):
        model = p["home_prob"] if side == "home" else 1 - p["home_prob"]
        raw = raw_home if side == "home" else 1 - raw_home
        market = None if not m or m.get("home_prob") is None else (
            m["home_prob"] if side == "home" else 1 - m["home_prob"])
        dec = None if not m else m.get(f"{side}_ml_dec")
        yield side, g[side], model, market, dec, raw


def _gap_ok(model, market):
    return market is not None and abs(model - market) <= config.MAX_MODEL_MARKET_GAP


def find_bets(rows, top=10):
    kalshi, book, legs = [], [], []
    for r in rows:
        g = r["game"]
        best_leg = None
        for side, team, model, market, dec, raw in _sides(r):
            base = {"game_id": g["id"], "matchup": f"{g['away']} @ {g['home']}", "team": team,
                    "side": side, "model": model, "raw_model": raw, "market": market,
                    "gap": None if market is None else raw - market, "gap_ok": _gap_ok(raw, market)}
            k = (r.get("kalshi") or {}).get(side)
            if k and k.get("yes_ask") is not None and 0 < k["yes_ask"] < 1:
                price = k["yes_ask"]
                e = edge_after_fee(model, price)
                kalshi.append({**base, "price": price, "fee": kalshi_fee(price), "edge": e,
                               "flag": e >= config.MIN_EDGE and base["gap_ok"],
                               "ticker": k.get("ticker")})
            if dec:
                ev = model * dec - 1
                book.append({**base, "decimal": dec, "ev": ev})
                if base["gap_ok"] and model > market and (best_leg is None or ev > best_leg["ev"]):
                    best_leg = {**base, "decimal": dec, "ev": ev}
        if best_leg:
            legs.append(best_leg)  # at most one leg per game

    kalshi.sort(key=lambda b: b["edge"], reverse=True)
    book = sorted([b for b in book if b["gap_ok"] and b["ev"] > 0], key=lambda b: b["ev"], reverse=True)

    parlays = []
    for n in (2, 3):
        for combo in combinations(legs, n):
            prob, dec = 1.0, 1.0
            for leg in combo:
                prob *= leg["model"]
                dec *= leg["decimal"]
            mkt = 1.0
            for leg in combo:
                mkt *= leg["market"]
            parlays.append({"legs": [{k: leg[k] for k in ("game_id", "matchup", "team", "model", "market", "decimal")}
                                     for leg in combo],
                            "prob": prob, "market_prob": mkt, "decimal": dec, "ev": prob * dec - 1})
    parlays = sorted([p for p in parlays if p["ev"] > 0], key=lambda p: p["ev"], reverse=True)[:top]

    return {"kalshi": kalshi[:top], "kalshi_flags": sum(b["flag"] for b in kalshi),
            "book": book[:top], "parlays": parlays,
            "excluded_gap": sorted({f"{r['game']['away']} @ {r['game']['home']}" for r in rows
                                    for _, _, _, mk, _, raw in _sides(r) if mk is not None and not _gap_ok(raw, mk)})}
