"""Bankroll and evaluation tools borrowed from what worked in betting and
trading (see data/research.json for the sources):

- Kelly sizing, fractional and capped          (kelly-1956, maclean-thorp-ziemba-2010)
- exposure caps per game and per slate         (ltcm-lowenstein-2000, medallion-zuckerman-2019)
- longshot guard for cheap Kalshi contracts    (burgi-deng-whelan-2025, thaler-ziemba-1988)
- spread -> cover probability                  (stern-1991)
- bootstrap confidence intervals and the t >= 3 bar (harvey-liu-zhu-2016)
- paper-trade log scored by closing line value (clv-practice)

Read-only: nothing here places a bet. "Stake" means a suggested size.
"""
import csv
import math
import os
from datetime import datetime, timezone

import numpy as np

import config

KELLY_FRACTION = 0.25        # quarter Kelly: edge estimates are uncertain
MAX_BET_FRACTION = 0.02      # never more than 2% of bankroll on one bet
MAX_GAME_FRACTION = 0.03     # outcomes in one game are correlated
MAX_SLATE_FRACTION = 0.10    # total exposure on one slate
LONGSHOT_MIN_PRICE = 0.20    # Kalshi YES under 20c is never flagged (favorite-longshot bias)
T_STAT_BAR = 3.0             # Harvey-Liu-Zhu multiple-testing hurdle

PAPER_TRADES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "paper_trades.csv")


# ------------------------------------------------------------- sizing -----

def kelly_fraction_decimal(p, decimal_odds):
    """Full-Kelly fraction for a bet at decimal odds: (p*d - 1) / (d - 1). 0 if no edge."""
    if decimal_odds is None or decimal_odds <= 1:
        return 0.0
    return max(0.0, (p * decimal_odds - 1) / (decimal_odds - 1))


def kelly_fraction_kalshi(p, price):
    """Full-Kelly fraction for buying a $1 YES contract at `price` plus Kalshi's fee.
    Cost c = price + fee; a win pays $1, so net odds are (1 - c)/c and f = (p - c)/(1 - c)."""
    from kalshi import kalshi_fee
    c = price + kalshi_fee(price)
    if not 0 < c < 1:
        return 0.0
    return max(0.0, (p - c) / (1 - c))


def size_bets(bets, bankroll=1000.0, fraction=KELLY_FRACTION):
    """Add 'stake' (dollars) to each bet dict that has 'kelly' (full-Kelly fraction)
    and 'game_id'. Applies fractional Kelly, then the per-bet, per-game and
    per-slate caps, largest edges first. Returns the same list."""
    game_used, slate_used = {}, 0.0
    for b in sorted(bets, key=lambda b: b.get("kelly", 0), reverse=True):
        f = min(b.get("kelly", 0) * fraction, MAX_BET_FRACTION)
        f = min(f, MAX_GAME_FRACTION - game_used.get(b["game_id"], 0.0), MAX_SLATE_FRACTION - slate_used)
        f = max(f, 0.0)
        b["stake_fraction"] = f
        b["stake"] = round(f * bankroll, 2)
        game_used[b["game_id"]] = game_used.get(b["game_id"], 0.0) + f
        slate_used += f
    return bets


# --------------------------------------------------------- probability ----

def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cover_prob(pred_margin, home_line, sigma):
    """P(home covers) when the final margin ~ Normal(pred_margin, sigma).
    home_line uses betting convention (-3 = home gives 3). Home covers if
    margin + home_line > 0. Pushes are ignored (continuous approximation)."""
    return normal_cdf((pred_margin + home_line) / sigma)


# --------------------------------------------------------- evaluation -----

def bootstrap_ci(returns, n=2000, seed=7, level=0.95):
    """Percentile bootstrap CI for the mean return per bet. Fixed seed = repeatable."""
    r = np.asarray(returns, float)
    if len(r) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n, len(r)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return float(lo), float(hi)


def t_stat(returns):
    r = np.asarray(returns, float)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return None
    return float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))


def max_drawdown(equity):
    """Largest peak-to-trough fall of an equity curve, as a fraction of the peak."""
    peak, worst = -math.inf, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


# ------------------------------------------------- paper trades / CLV -----

FIELDS = ["logged_at", "sport", "game_id", "matchup", "team", "side", "market_type",
          "price_decimal", "bet_prob", "market_prob", "ev", "stake", "venue"]


def log_recommendations(sport, bets):
    """Append recommended bets (paper trades) to data/paper_trades.csv, once per
    game/team/venue per day, so they can later be scored against the closing line."""
    os.makedirs(os.path.dirname(PAPER_TRADES), exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen = set()
    if os.path.exists(PAPER_TRADES):
        with open(PAPER_TRADES, newline="") as f:
            for row in csv.DictReader(f):
                seen.add((row["logged_at"][:10], row["game_id"], row["team"], row["venue"], row["market_type"]))
    new = []
    for b in bets:
        key = (today, str(b["game_id"]), b["team"], b["venue"], b.get("market_type", "moneyline"))
        if key in seen:
            continue
        seen.add(key)
        new.append({
            "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), "sport": sport,
            "game_id": b["game_id"], "matchup": b["matchup"], "team": b["team"], "side": b["side"],
            "market_type": b.get("market_type", "moneyline"), "price_decimal": round(b["decimal"], 4),
            "bet_prob": round(b["model"], 4),
            "market_prob": "" if b.get("market") is None else round(b["market"], 4),
            "ev": round(b["ev"], 4), "stake": b.get("stake", ""), "venue": b["venue"],
        })
    if new:
        write_header = not os.path.exists(PAPER_TRADES)
        with open(PAPER_TRADES, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new)
    return len(new)


def load_paper_trades():
    if not os.path.exists(PAPER_TRADES):
        return []
    with open(PAPER_TRADES, newline="") as f:
        return list(csv.DictReader(f))


def clv_report():
    """Score NFL paper trades against the nflverse closing moneyline.

    CLV EV = close_prob * offered_decimal - 1: the expected return of our
    price if the closing no-vig probability is the truth. Positive = we got
    a better price than the market's final opinion.
    """
    from nfl import load_games
    from odds import american_to_decimal, remove_vig
    trades = [t for t in load_paper_trades() if t["sport"] == "nfl" and t["market_type"] == "moneyline"]
    if not trades:
        return {"trades": 0, "scored": 0, "rows": []}
    games, stamp = load_games()
    g = games.set_index("game_id")
    rows = []
    for t in trades:
        if t["game_id"] not in g.index:
            continue
        r = g.loc[t["game_id"]]
        if any(math.isnan(x) for x in (r["home_moneyline"], r["away_moneyline"])):
            continue
        close_home, _ = remove_vig(r["home_moneyline"], r["away_moneyline"])
        close_p = close_home if t["side"] == "home" else 1 - close_home
        close_dec = american_to_decimal(r["home_moneyline"] if t["side"] == "home" else r["away_moneyline"])
        offered = float(t["price_decimal"])
        result = None
        if not math.isnan(r["home_score"]):
            won_home = r["home_score"] > r["away_score"]
            if r["home_score"] != r["away_score"]:
                won = won_home if t["side"] == "home" else not won_home
                result = (offered - 1) if won else -1.0
        rows.append({**t, "close_prob": close_p, "close_decimal": close_dec,
                     "clv_ev": close_p * offered - 1,
                     "price_beat_close": offered > close_dec, "result_per_unit": result})
    done = [x["result_per_unit"] for x in rows if x["result_per_unit"] is not None]
    clv = [x["clv_ev"] for x in rows]
    return {"trades": len(trades), "scored": len(rows), "source": f"nflverse closing lines @ {stamp}",
            "avg_clv_ev": float(np.mean(clv)) if clv else None,
            "beat_close_rate": float(np.mean([x["price_beat_close"] for x in rows])) if rows else None,
            "settled": len(done), "roi": float(np.mean(done)) if done else None, "rows": rows}
