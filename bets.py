"""Bet finder: ranks Kalshi singles, sportsbook singles, and 2-3 leg parlays
from a slate of rows (NFL or college). Read-only: it only suggests.

Every candidate must pass the project's safety rule: the model and the
market consensus must be within 10 points. A bigger gap usually means the
model is missing information, not that the market is wrong.

"Likely to win" is not the goal. A bet only makes money over time when the
price is better than the true chance, so everything here is ranked by
expected value (EV), and the win chance is shown next to it.
"""
import math
from itertools import combinations

import config
from kalshi import edge_after_fee, kalshi_fee
from strategy import (LONGSHOT_MIN_PRICE, cover_push_prob, kelly_fraction_decimal, kelly_with_push,
                      kelly_fraction_kalshi, log_recommendations, size_bets)


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


def _prob_to_margin(p):
    """Win probability -> implied margin (same 25 Elo-per-point curve as ratings.py)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 400 / config.ELO_PER_POINT * math.log10(p / (1 - p))


def find_bets(rows, top=10, bankroll=1000.0, log_sport=None):
    """Rank Kalshi buys, sportsbook moneyline and spread singles, and parlays.

    Stakes: quarter Kelly with per-bet / per-game / per-slate caps
    (strategy.size_bets). Only bets with EV (or Kalshi edge) >= 3% get a stake.
    If log_sport is given, staked bets are appended to the paper-trade log so
    they can be scored against the closing line later (strategy.clv_report).
    """
    kalshi, book, spreads, legs = [], [], [], []
    for r in rows:
        g = r["game"]
        best_leg = None
        p_home = r["pred"]["home_prob"]
        m = r.get("market") or {}
        sigma = r.get("sigma")
        for side, team, model, market, dec, raw in _sides(r):
            base = {"game_id": g["id"], "matchup": f"{g['away']} @ {g['home']}", "team": team,
                    "side": side, "model": model, "raw_model": raw, "market": market,
                    "gap": None if market is None else raw - market, "gap_ok": _gap_ok(raw, market)}
            k = (r.get("kalshi") or {}).get(side)
            if k and k.get("yes_ask") is not None and 0 < k["yes_ask"] < 1:
                price = k["yes_ask"]
                e = edge_after_fee(model, price)
                longshot = price < LONGSHOT_MIN_PRICE
                kalshi.append({**base, "venue": "kalshi", "market_type": "moneyline", "price": price,
                               "fee": kalshi_fee(price), "edge": e, "ev": e / (price + kalshi_fee(price)),
                               "decimal": 1 / (price + kalshi_fee(price)), "longshot": longshot,
                               "kelly": kelly_fraction_kalshi(model, price),
                               "flag": e >= config.MIN_EDGE and base["gap_ok"] and not longshot,
                               "ticker": k.get("ticker")})
            if dec:
                ev = model * dec - 1
                book.append({**base, "venue": "book", "market_type": "moneyline", "decimal": dec, "ev": ev,
                             "kelly": kelly_fraction_decimal(model, dec)})
                if base["gap_ok"] and model > market and (best_leg is None or ev > best_leg["ev"]):
                    best_leg = {**base, "decimal": dec, "ev": ev}
            # Spread bet (stern-1991): cover probability from the bet probability's implied margin.
            line, sdec = m.get("home_spread"), m.get(f"{side}_spread_dec")
            if sigma and line is not None and sdec and m.get("home_prob") is not None:
                # Center on the market line; shift only by the bet prob's disagreement with the market.
                m_bet = -line + _prob_to_margin(p_home) - _prob_to_margin(m["home_prob"])
                # Key-number margin distribution: pushes on whole-number lines refund the stake.
                pc, pp = cover_push_prob(m_bet, line, sigma, r.get("key_weights"))
                pc = pc if side == "home" else 1 - pc - pp
                ev = pc * sdec + pp - 1
                spreads.append({**base, "venue": "book", "market_type": "spread", "model": pc, "push": pp,
                                "line": line if side == "home" else -line, "decimal": sdec, "ev": ev,
                                "kelly": kelly_with_push(pc, pp, sdec)})
        if best_leg:
            legs.append(best_leg)  # at most one leg per game

    kalshi.sort(key=lambda b: b["edge"], reverse=True)
    book = sorted([b for b in book if b["gap_ok"] and b["ev"] > 0], key=lambda b: b["ev"], reverse=True)
    spreads = sorted([b for b in spreads if b["gap_ok"] and b["ev"] > 0], key=lambda b: b["ev"], reverse=True)

    # Only bets that clear the 3% bar get a stake; caps keep correlated
    # exposure in check (ltcm-lowenstein-2000).
    stakeable = [b for b in kalshi if b["flag"]] + \
                [b for b in book + spreads if b["ev"] >= config.MIN_EDGE]
    size_bets(stakeable, bankroll)
    for b in kalshi + book + spreads:
        b.setdefault("stake", 0.0)

    parlays = []
    for n in (2, 3):
        for combo in combinations(legs, n):
            prob, dec, mkt = 1.0, 1.0, 1.0
            for leg in combo:
                prob *= leg["model"]
                dec *= leg["decimal"]
                mkt *= leg["market"]
            parlays.append({"legs": [{k: leg[k] for k in ("game_id", "matchup", "team", "model", "market", "decimal")}
                                     for leg in combo],
                            "prob": prob, "market_prob": mkt, "decimal": dec, "ev": prob * dec - 1})
    parlays = sorted([p for p in parlays if p["ev"] > 0], key=lambda p: p["ev"], reverse=True)[:top]

    logged = log_recommendations(log_sport, [b for b in stakeable if b["stake"] > 0]) if log_sport else 0
    return {"kalshi": kalshi[:top], "kalshi_flags": sum(b["flag"] for b in kalshi),
            "book": book[:top], "spreads": spreads[:top], "parlays": parlays,
            "bankroll": bankroll, "total_stake": round(sum(b["stake"] for b in stakeable), 2),
            "logged": logged,
            "excluded_gap": sorted({f"{r['game']['away']} @ {r['game']['home']}" for r in rows
                                    for _, _, _, mk, _, raw in _sides(r) if mk is not None and not _gap_ok(raw, mk)})}
