"""Walk-forward backtest: predict each week using only games already played,
then learn from that week's results. No lookahead.

Seasons 2021-2022 only warm up the ratings (never scored). 2023-2025 are
scored. No parameter in config.py was changed based on these results.
"""
import math
from collections import defaultdict
from statistics import median

from tabulate import tabulate

import config
from api_clients import cfbd_advanced_game_stats, cfbd_games, cfbd_lines
from odds import remove_vig
from ratings import RatingSystem


def _week_groups(games):
    """Group games into weeks in true calendar order (postseason after regular)."""
    groups = defaultdict(list)
    for g in games:
        groups[(g["season_type"], g["week"])].append(g)
    return sorted(groups.values(), key=lambda gs: min(g["start"] or "" for g in gs))


def replay_season(rs, year, on_week=None):
    """Feed one season into rating system rs, week by week.

    on_week(games_in_week, rs) is called BEFORE that week's results are
    learned, so any prediction made inside it only sees earlier games.
    """
    games, _ = cfbd_games(year)
    adv, _ = cfbd_advanced_game_stats(year)
    adv_by_game = defaultdict(list)
    for r in adv:
        adv_by_game[r["game_id"]].append(r)
    rs.start_season()
    for week_games in _week_groups(games):
        if on_week:
            on_week(week_games, rs)
        for g in week_games:
            if g["completed"]:
                rs.update(g, adv_by_game.get(g["id"], []))
    return games


def closing_market(lines):
    """Closing line for a game: median spread across providers, and the
    average no-vig home win probability from providers with moneylines."""
    spreads = [ln["spread"] for ln in lines if ln["spread"] is not None]
    probs = [remove_vig(ln["home_ml"], ln["away_ml"])[0] for ln in lines
             if ln["home_ml"] is not None and ln["away_ml"] is not None]
    return (median(spreads) if spreads else None,
            sum(probs) / len(probs) if probs else None)


def brier(probs, outcomes):
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def log_loss(probs, outcomes, eps=1e-6):
    total = 0.0
    for p, y in zip(probs, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(probs)


def accuracy(probs, outcomes):
    """Straight-up: did the team we gave > 50% actually win?"""
    return sum((p > 0.5) == (y == 1) for p, y in zip(probs, outcomes)) / len(probs)


def ats_record(records):
    """Against the spread vs the closing line.
    Model likes HOME if its predicted home margin > -closing spread.
    Home covers if actual margin + closing spread > 0 (== 0 is a push)."""
    w = l = p = 0
    for r in records:
        if r["close_spread"] is None:
            continue
        model_edge = r["margin_pred"] + r["close_spread"]
        if model_edge == 0:
            continue
        cover = r["margin_actual"] + r["close_spread"]
        if cover == 0:
            p += 1
        elif (cover > 0) == (model_edge > 0):
            w += 1
        else:
            l += 1
    return w, l, p


def calibration_table(probs, outcomes, buckets=10):
    rows = []
    for b in range(buckets):
        lo, hi = b / buckets, (b + 1) / buckets
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == buckets - 1 and p == 1)]
        if not idx:
            continue
        avg_p = sum(probs[i] for i in idx) / len(idx)
        win = sum(outcomes[i] for i in idx) / len(idx)
        rows.append([f"{lo:.0%}-{hi:.0%}", len(idx), f"{avg_p:.1%}", f"{win:.1%}",
                     f"{(win - avg_p) * 100:+.1f}"])
    return rows


def run_backtest(seasons=None):
    seasons = seasons or config.TEST_SEASONS
    rs = RatingSystem()
    records = []

    for year in config.BURN_IN_SEASONS:
        replay_season(rs, year)

    for year in seasons:
        lines, _ = cfbd_lines(year)

        def score_week(week_games, rs, year=year, lines=lines):
            for g in week_games:
                # Score only completed FBS-vs-FBS games (FCS games are mostly
                # mismatches that would make accuracy look better than it is).
                if not (g["completed"] and g["home_fbs"] and g["away_fbs"]):
                    continue
                if g["home_pts"] is None or g["home_pts"] == g["away_pts"]:
                    continue
                pred = rs.predict(g["home"], g["away"], g["neutral"])
                spread, mkt_p = closing_market(lines.get(g["id"], []))
                records.append({
                    "season": year, "p": pred["home_prob"], "margin_pred": pred["margin"],
                    "y": 1 if g["home_pts"] > g["away_pts"] else 0,
                    "margin_actual": g["home_pts"] - g["away_pts"],
                    "close_spread": spread, "market_p": mkt_p,
                })

        replay_season(rs, year, on_week=score_week)
    return records


def report(records):
    out = []
    out.append("BACKTEST: walk-forward, FBS vs FBS games, ratings warmed up on "
               f"{config.BURN_IN_SEASONS[0]}-{config.BURN_IN_SEASONS[-1]} (not scored).")
    out.append("Parameters are fixed in config.py and were NOT tuned on these seasons.\n")

    summary = []
    for label, recs in [(str(s), [r for r in records if r["season"] == s])
                        for s in sorted({r["season"] for r in records})] + [("ALL", records)]:
        if not recs:
            continue
        p, y = [r["p"] for r in recs], [r["y"] for r in recs]
        w, l, push = ats_record(recs)
        ats = f"{w}-{l}-{push} ({w / (w + l):.1%})" if w + l else "n/a"
        summary.append([label, len(recs), f"{accuracy(p, y):.1%}", f"{brier(p, y):.4f}",
                        f"{log_loss(p, y):.4f}", ats])
    out.append(tabulate(summary, headers=["Season", "Games", "SU accuracy", "Brier",
                                          "Log loss", "ATS vs close (W-L-P)"]))
    out.append("  Brier: 0 = perfect, 0.25 = coin flip. Log loss: lower is better.")
    out.append("  ATS: you need ~52.4% to break even at -110 odds.\n")

    # Head-to-head vs the closing market on games where it has a moneyline.
    both = [r for r in records if r["market_p"] is not None]
    if both:
        y = [r["y"] for r in both]
        mp, kp = [r["p"] for r in both], [r["market_p"] for r in both]
        model_b, market_b = brier(mp, y), brier(kp, y)
        out.append(tabulate([
            ["Model", f"{accuracy(mp, y):.1%}", f"{model_b:.4f}", f"{log_loss(mp, y):.4f}"],
            ["Closing market (no-vig)", f"{accuracy(kp, y):.1%}", f"{market_b:.4f}", f"{log_loss(kp, y):.4f}"],
        ], headers=[f"vs CLOSING LINE ({len(both)} games w/ moneylines)", "SU accuracy", "Brier", "Log loss"]))
        if model_b > market_b:
            out.append(f"\n>>> The model is WORSE than the closing-line market on Brier score "
                       f"({model_b:.4f} vs {market_b:.4f}). Treat its 'edges' with heavy skepticism.\n")
        else:
            out.append(f"\n>>> The model beat the closing-line market on Brier score "
                       f"({model_b:.4f} vs {market_b:.4f}). This is unusual; double-check before trusting it.\n")
    else:
        out.append("No closing moneylines found, so no head-to-head vs market.\n")

    out.append("CALIBRATION (home-team win probability):")
    out.append(tabulate(calibration_table([r["p"] for r in records], [r["y"] for r in records]),
                        headers=["Predicted", "Games", "Avg predicted", "Actual win %", "Gap (pts)"]))
    out.append("  Well calibrated = 'Avg predicted' close to 'Actual win %' in every row.")
    return "\n".join(out)
