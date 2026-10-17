"""College football + NFL model: command-line entry point.

    python main.py week [--sport nfl]                 this week's (NFL: today's) table
    python main.py backtest [--sport nfl]             2023-2025 validation report
    python main.py bets [--sport nfl] [--bankroll N]  ranked Kalshi bets, singles, parlays, stakes
    python main.py clv                                score paper trades against the closing line
    python main.py props [--backtest]                 NFL player props: projections, fair lines, edges
    python main.py parlay ID:TEAM ID:TEAM [--sport nfl]   parlay fair price (2 or 3 legs)
    python main.py serve                              local web app at http://127.0.0.1:8000

Read-only: this program never places trades, bets, or orders.
"""
import argparse
import sys

from tabulate import tabulate

from cache import ApiError
from config import MissingKeyError


def fmt_spread(home, away, home_spread):
    """-7.5 (home view) -> 'Georgia -7.5'. Always names the favorite."""
    if home_spread is None:
        return "n/a"
    if abs(home_spread) < 0.05:
        return "PK"
    fav = home if home_spread < 0 else away
    return f"{fav} -{abs(home_spread):.1f}"


def pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def load_slate(sport, days=0, fresh=False):
    """Returns (title, notes, rows) for either sport. fresh=True re-downloads live data now."""
    if fresh:
        from cache import fresh as fresh_ctx
        with fresh_ctx():
            return load_slate(sport, days)
    if sport == "nfl":
        from nfl import build_slate
        info, rows = build_slate(days=days)
        return info["label"], info["notes"], rows
    from week import build_week
    week, rows = build_week()
    return f"Week {week['week']} ({week['season_type']})", week.get("notes", []), rows


def cmd_scan(args):
    """Kalshi vs. sportsbook (DraftKings via ESPN), no API keys needed."""
    from zoneinfo import ZoneInfo
    from tabulate import tabulate
    from cache import fresh as fresh_ctx
    from scan import scan
    from strategy import log_recommendations
    with fresh_ctx():
        r = scan(args.sport, days=args.days, bankroll=args.bankroll)
    et = ZoneInfo("America/New_York")
    print(f"\nKALSHI SCAN — {args.sport.upper()}, today + {args.days} day(s). Kalshi @ {r['kalshi_stamp']}, "
          f"ESPN odds @ {r['espn_stamp']}. {r['games']} games compared.")
    print("Bet prob = sportsbook no-vig price (not the model). FLAG = edge after fee >= 3 pts and price >= 20c.")
    for n in r["notes"]:
        print(f"NOTE: {n}")
    rows = r["rows"] if args.all else r["rows"][:args.top]
    kind = {"moneyline": "ML", "spread": "Spread", "total": "Total"}
    print(tabulate([[x["matchup"], kind.get(x.get("market_type"), "ML"), x["team"] if x.get("market_type") != "moneyline"
                     else f"{x['team']} to win", x["kickoff"].astimezone(et).strftime("%a %-I:%M%p"),
                     f"{x['market']:.1%}", f"{'YES' if x.get('market_type') == 'moneyline' else x['side'].upper()} {x['price']:.2f}",
                     f"{x['edge'] * 100:+.1f}",
                     "FLAG" if x["flag"] else ("longshot" if x["longshot"] and x["edge"] >= 0.03 else "PASS"),
                     f"${x['stake']:,.2f}" if x["stake"] else "-"] for x in rows],
                   headers=["Game", "Type", "Bet", "Kickoff (ET)", "Book fair", "Kalshi ask", "Edge pts", "Call", "Stake"]))
    flagged = [x for x in r["rows"] if x["flag"]]
    logged = log_recommendations(args.sport, flagged) if flagged else 0
    print(f"\nFLAGS: {len(flagged)}. Paper trades logged: {logged}. Not betting advice; this tool never places bets.")


def cmd_picks(args):
    """Consensus of outside forecasters (ESPN FPI, Dratings, Sagarin, + Hashmark) vs the lines."""
    from zoneinfo import ZoneInfo
    from tabulate import tabulate
    import consensus as C
    if args.grade:
        g = C.grade()
        if not g["rows"]:
            print("No graded picks yet. Picks are logged when you run `picks`; they're graded once games are final.")
            return
        print(tabulate([[r["game_date"], r["matchup"], r["bet_type"], r["pick"], r["tier"], r["score"], r["result"]]
                        for r in g["rows"]], headers=["Date", "Game", "Type", "Pick", "Tier", "Final (away-home)", "Result"]))
        print()
        for k, r in sorted(g["record"].items()):
            n = r["W"] + r["L"]
            print(f"{k:16s} {r['W']}-{r['L']}-{r['P']}  ({r['W'] / n:.1%})" if n else f"{k:16s} {r['W']}-{r['L']}-{r['P']}")
        print("Break-even against -110 prices is 52.4%. Small samples swing a lot: judge after 100+ picks.")
        return
    r = C.build(args.sport, days=args.days)
    et = ZoneInfo("America/New_York")
    print(f"\nCONSENSUS PICKS — {args.sport.upper()}. Sources: ESPN FPI, Dratings, Sagarin, Dimers, OddsShark"
          + (", Hashmark model" if any("Hashmark model" in g["sources"] for g in r["games"]) else "")
          + ". Lines: DraftKings (via ESPN). Kalshi for the winner.")
    for n in r["notes"]:
        print(f"NOTE: {n}")
    fmt_line = lambda x: "-" if x is None else x
    rows = []
    for g in r["games"]:
        ats, ou, ml = g["ats"], g["ou"], g["ml"]
        rows.append([g["kickoff"].astimezone(et).strftime("%a %-I:%M%p"), g["matchup"],
                     f"{g['winner']} {g['winner_p']:.0%} ({g['winner_agree']}/{g['n_sources']})",
                     "-" if g["margin"] is None else f"{g['home']} {g['margin']:+.1f}",
                     "-" if not ats else f"{ats['pick']} ({ats['gap']:+.1f}, {ats['agree']}/{ats['n']}){' *' + ats['tier'] if ats['tier'] else ''}",
                     "-" if not ou else f"{ou['pick']} (proj {g['total']:.1f}){' *' + ou['tier'] if ou['tier'] else ''}",
                     "-" if not ml or "kalshi_ask" not in ml else f"{ml['kalshi_ask'] * 100:.0f}c, edge {ml['kalshi_edge'] * 100:+.1f}"])
    print(tabulate(rows, headers=["Kickoff (ET)", "Game", "Consensus winner", "Pred. margin", "Spread pick (gap, agree)",
                                  "Total pick", "Kalshi winner"]))
    best = C.best_bets(r)
    print(f"\nBEST BETS (fixed rules: Strong = 3+ sources all agree and the line is off by "
          f"{C.TIERS[args.sport]['strong_gap']:g}+ pts, spreads under {C.BLOWOUT_LINE:g} (5+ sources: 80% agree); Lean = 2/3 agree, {C.TIERS[args.sport]['lean_gap']:g}+ pts):")
    if not best:
        print("  None today.")
    for p in best[:args.top]:
        gm = p["game"]
        print(f"  {p['tier']:6s} {p['type']:6s} {p['pick']:28s} {gm['matchup']:36s} line off by {abs(p['gap']):.1f} pts, "
              f"{p['agree']}/{p['n']} sources agree")
    logged = C.log_picks(r)
    print(f"\nLogged {logged} new picks to data/consensus_picks.csv. Grade them later: python main.py picks --grade")
    print("Not validated yet: these forecasters usually lose to the closing line on their own. The graded record decides.")


def cmd_kalshi(args):
    """Check that Kalshi is reachable, fresh, and matched to this slate's games."""
    from datetime import datetime, timezone
    from api_clients import kalshi_markets
    from cache import fresh as fresh_ctx
    series = "KXNFLGAME" if args.sport == "nfl" else "KXNCAAFGAME"
    with fresh_ctx():
        markets, stamp = kalshi_markets(series)
    age = (datetime.now(timezone.utc) - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    events = {m["event_ticker"] for m in markets}
    wide = [m for m in markets if m["yes_ask"] is not None and m["yes_bid"] is not None and m["yes_ask"] - m["yes_bid"] > 0.05]
    print(f"Kalshi {series}: {len(markets)} open markets in {len(events)} games, fetched {stamp} ({age:.0f} s ago).")
    print(f"  No YES ask: {sum(m['yes_ask'] is None for m in markets)}.  Bid-ask spread over 5c (thin): {len(wide)}.")
    print("  Prices refresh every 5 minutes on their own; --fresh or the app's Refresh button fetches them now.")
    try:
        _, notes, rows = load_slate(args.sport, args.days, fresh=True)
    except Exception as e:   # e.g. no CFBD key for college
        print(f"  Could not build the {args.sport} slate to check matching: {e}")
        return
    matched = [r for r in rows if r["kalshi"]]
    print(f"  Slate games matched to a Kalshi market: {len(matched)} of {len(rows)}.")
    for r in rows:
        if not r["kalshi"]:
            print(f"    no market: {r['game']['away']} @ {r['game']['home']}")
    for n in notes:
        if n.startswith("Skipped"):
            print(f"  {n}")


def cmd_week(args):
    title, notes, rows = load_slate(args.sport, args.days, fresh=args.fresh)
    print(f"\n{title} — percentages are for the HOME team.\n")
    for n in notes:
        print(f"NOTE: {n}")
    table, sources = [], []
    for r in rows:
        g, p, m, k, best = r["game"], r["pred"], r["market"], r["kalshi"], r["best"]
        if k is None:
            kalshi = "no market"
        else:
            kalshi = "no ask" if k["home"]["yes_ask"] is None else f"{k['home']['yes_ask']:.2f}"
        edge = "n/a" if best is None else f"{best[0] * 100:+.1f} pts {best[1]}"
        table.append([
            g["id"], f"{g['away']} @ {g['home']}" + (" (N)" if g["neutral"] else ""),
            fmt_spread(g["home"], g["away"], p["home_spread"]),
            fmt_spread(g["home"], g["away"], m["home_spread"] if m else None),
            pct(p.get("raw_home_prob", p["home_prob"])), pct(m["home_prob"] if m else None),
            pct(p["home_prob"]), kalshi, edge, "FLAG" if r["flag"] else "PASS",
        ])
        inj = f" (incl. injuries {p['injury_adj']:+.1f})" if p["injury_adj"] else ""
        if "env" in r:
            e = r["env"]
            qb = (f", QB {e['qb_pts']:+.1f} ({e['away_qb']} @ {e['home_qb']})" if e.get("home_qb") else "")
            inj += (f" | env {p['env_margin']:+.1f} pts (rest {e['rest']:+.0f}d, travel {e['tz']:+.0f}h{qb})"
                    f", total {p['env_total']:+.1f}; {r['sources']['weather']}")
        sources.append([g["id"], r["sources"]["model"] + inj, r["sources"]["market"],
                        r["sources"]["kalshi"]])
    print(tabulate(table, headers=["ID", "Game", "Model spread", "Market spread", "Model win%",
                                   "Market win%", "Bet win%", "Kalshi (home YES ask)", "Edge after fees", "Bet"]))
    print("\nBet win% = the probability used for edges. College: the model. NFL: the model blended with")
    print("the market by the trust weight fitted in the backtest, plus your injuries.csv.")
    print("\nSOURCES & TIMESTAMPS (UTC) for every number above:")
    print(tabulate(sources, headers=["ID", "Model spread & win%",
                                     "Market spread & win% (no-vig avg)", "Kalshi price"]))
    print("\nFLAG = edge after Kalshi fee >= 3 pts AND model within 10 pts of market. "
          "Edge is for the better Kalshi side. Not betting advice.")


def cmd_backtest(args):
    if args.sport == "nfl":
        from nfl import report, run_backtest
        print(report(run_backtest()))
        return
    from backtest import report, run_backtest
    print(report(run_backtest()))


def cmd_bets(args):
    from bets import find_bets
    title, notes, rows = load_slate(args.sport, args.days, fresh=args.fresh)
    b = find_bets(rows, bankroll=args.bankroll, log_sport=args.sport)
    money = lambda x: f"${x:,.2f}" if x else "-"
    print(f"\nBET FINDER — {title}   (bankroll {money(args.bankroll)}, quarter Kelly, caps 2%/bet 3%/game 10%/slate)")
    for n in notes:
        print(f"NOTE: {n}")
    print("Ranked by expected value, not win chance. Only sides where the raw model is within 10 pts of the market.")
    print("Check `python main.py backtest" + (" --sport nfl" if args.sport == "nfl" else "") +
          "` first: if the model loses to (or ties) the closing line, treat model edges as noise.\n")
    print("KALSHI (FLAG = edge after fee >= 3 pts, gap <= 10, price >= 20c):")
    if b["kalshi"]:
        print(tabulate([[k["matchup"], k["team"], pct(k["model"]), pct(k["market"]), f"{k['price']:.2f}",
                         f"{k['edge'] * 100:+.1f}", "longshot" if k["longshot"] else ("FLAG" if k["flag"] else "PASS"),
                         money(k["stake"])] for k in b["kalshi"]],
                       headers=["Game", "Team", "Bet prob", "Market", "YES ask", "Edge pts", "Call", "Stake"]))
    else:
        print("  No Kalshi prices available.")
    print("\nSPORTSBOOK MONEYLINE singles with positive EV (average book price):")
    print(tabulate([[x["matchup"], x["team"], pct(x["model"]), pct(x["market"]), f"{x['decimal']:.2f}",
                     f"{x['ev']:+.3f}", money(x["stake"])] for x in b["book"]],
                   headers=["Game", "Team", "Bet prob", "Market", "Decimal", "EV per $1", "Stake"]) if b["book"] else "  None.")
    print("\nSPREAD singles with positive EV (key-number margin distribution; pushes refund the stake):")
    print(tabulate([[x["matchup"], f"{x['team']} {x['line']:+.1f}", pct(x["model"]), f"{x['decimal']:.2f}",
                     f"{x['ev']:+.3f}", money(x["stake"])] for x in b["spreads"]],
                   headers=["Game", "Side", "Cover prob", "Decimal", "EV per $1", "Stake"]) if b["spreads"] else "  None.")
    print("\nPARLAYS (different games only, legs assumed independent; not staked):")
    print(tabulate([[" + ".join(l["team"] for l in x["legs"]), pct(x["prob"]), pct(x["market_prob"]),
                     f"{x['decimal']:.2f}", f"{x['ev']:+.3f}"] for x in b["parlays"]],
                   headers=["Legs", "Hit %", "Market hit %", "Decimal", "EV per $1"]) if b["parlays"] else "  None.")
    if b["excluded_gap"]:
        print("\nExcluded (model and market more than 10 pts apart; model likely missing info):")
        print("  " + "; ".join(b["excluded_gap"]))
    print(f"\nTotal suggested stake: {money(b['total_stake'])}. Paper trades logged: {b['logged']} "
          "(data/paper_trades.csv; score them later with `python main.py clv`).")
    print("Not betting advice. This tool never places bets.")


def cmd_props(args):
    import props
    if args.backtest:
        print(props.report(props.run_backtest()))
        return
    _title, notes, rows = load_slate("nfl", args.days, fresh=args.fresh)
    if args.fresh:
        from cache import fresh
        with fresh():
            r = props.build_props(rows, bankroll=args.bankroll)
    else:
        r = props.build_props(rows, bankroll=args.bankroll)
    for n in notes + r["notes"]:
        print(f"NOTE: {n}")
    money = lambda x: f"${x:,.2f}" if x else "-"
    print("\nPLAYER PROPS — NFL. Median = the model's middle outcome; fair line = where over and under are even.")
    print("Prop edges are UNVALIDATED: there's no free history of prop lines to backtest against.\n")
    print(tabulate([[x["matchup"][:30], x["player"], x["position"], x["label"], f"{x['median']:.1f}",
                     f"{x['q25']:.0f}-{x['q75']:.0f}", x["fair_line"],
                     "-" if not x["market"] else f"{x['market']['line']}",
                     "-" if not x["market"] else f"{x['p_over']:.0%}",
                     "-" if not x["market"] else f"{x['pick']} {x['ev']:+.3f}",
                     money(x["stake"])] for x in r["rows"]],
                   headers=["Game", "Player", "Pos", "Stat", "Median", "25-75%", "Fair line", "Book line",
                            "P(over)", "Pick / EV", "Stake"]))
    print(f"\nSource: {r['source']}. Not betting advice; this tool never places bets.")


def cmd_clv(_args):
    from strategy import clv_report
    r = clv_report()
    if not r["scored"]:
        print("No NFL paper trades to score yet. Run `python main.py bets --sport nfl` before games, "
              "then run this after they finish.")
        return
    print(f"\nCLOSING LINE VALUE — {r['scored']} of {r['trades']} paper trades scored. Source: {r['source']}")
    print(tabulate([[x["logged_at"], x["matchup"], x["team"], f"{float(x['price_decimal']):.2f}",
                     f"{x['close_decimal']:.2f}", f"{x['clv_ev']:+.3f}",
                     "pending" if x["result_per_unit"] is None else f"{x['result_per_unit']:+.2f}"] for x in r["rows"]],
                   headers=["Logged", "Game", "Team", "Our price", "Close", "CLV EV", "Result/unit"]))
    print(f"\nAverage CLV EV {r['avg_clv_ev']:+.3f} per $1; beat the closing price {r['beat_close_rate']:.0%} of the time.")
    if r["settled"]:
        print(f"Settled: {r['settled']} bets, ROI {r['roi']:+.1%} (results are noisy; CLV is the faster skill signal).")


def cmd_parlay(args):
    from week import ParlayInputError, build_week, price_parlay

    picks = []
    for leg in args.legs:
        if ":" not in leg:
            sys.exit(f"Bad leg '{leg}'. Use GAMEID:TEAM (IDs are in `python main.py week`).")
        picks.append(tuple(leg.split(":", 1)))
    if not 2 <= len(picks) <= 3:
        sys.exit("Give 2 or 3 legs, e.g. python main.py parlay 401234:Georgia 401235:Texas")
    _title, _notes, rows = load_slate(args.sport, args.days, fresh=args.fresh)
    try:
        r = price_parlay(rows, picks, args.offered)
    except ParlayInputError as e:
        sys.exit(str(e))

    if r["warning"]:
        print(r["warning"] + "\n")
    print(tabulate([[l["game_id"], l["team"], pct(l["p_model"]), pct(l["p_market"]),
                     "n/a" if l["decimal"] is None else f"{l['decimal']:.3f}"] for l in r["legs"]],
                   headers=["Game", "Team", "Model win%", "Market win% (no-vig)", "Avg book decimal odds"]))
    m = r["model"]
    print(f"\nModel fair price:   {m['prob']:.1%}  =  {m['american']:+.0f}  (decimal {m['decimal']:.2f})")
    if r["market"]:
        k = r["market"]
        print(f"Market fair price:  {k['prob']:.1%}  =  {k['american']:+.0f}  (decimal {k['decimal']:.2f})")
    o = r["offered"]
    if o:
        print(f"Offered price:      {o['american']:+.0f} ({o['how']})  "
              f"(decimal {o['decimal']:.2f}, implies {o['implied']:.1%})")
        print(f"Model EV per $1:    {o['ev']:+.3f}")
    else:
        print("Offered price:      n/a (no moneylines; pass --offered +450 to enter your book's price)")
    print("\nSources: model = CFBD; market & book odds = The Odds API. Independence assumed. Not betting advice.")


def cmd_serve(args):
    import os
    from server import serve
    if os.environ.get("PORT"):   # cloud host (Render sets PORT): listen publicly, no browser
        serve(int(os.environ["PORT"]), open_browser=False, host="0.0.0.0", warm=True)
    else:
        serve(args.port)


def main():
    parser = argparse.ArgumentParser(description="College football model (read-only).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    def sport_args(sp, days=True):
        sp.add_argument("--sport", choices=["cfb", "nfl"], default="cfb")
        if days:
            sp.add_argument("--days", type=int, default=0, help="NFL: include the next N days too")
            sp.add_argument("--fresh", action="store_true", help="re-download all live data right now")
        return sp
    sport_args(sub.add_parser("week", help="current-week table")).set_defaults(func=cmd_week)
    sport_args(sub.add_parser("backtest", help="2023-2025 validation report"), days=False).set_defaults(func=cmd_backtest)
    bp = sport_args(sub.add_parser("bets", help="ranked bets and parlays"))
    bp.add_argument("--bankroll", type=float, default=1000.0, help="for suggested stakes (default 1000)")
    bp.set_defaults(func=cmd_bets)
    sub.add_parser("clv", help="score logged paper trades against the closing line").set_defaults(func=cmd_clv)
    sport_args(sub.add_parser("kalshi", help="check Kalshi is reachable, fresh, and matched to games")).set_defaults(func=cmd_kalshi)
    pk = sub.add_parser("picks", help="consensus of outside forecasters vs the lines (spread, total, winner)")
    pk.add_argument("--sport", choices=["cfb", "nfl"], default="cfb")
    pk.add_argument("--days", type=int, default=0, help="today plus this many days (NFL: try 3)")
    pk.add_argument("--top", type=int, default=15)
    pk.add_argument("--grade", action="store_true", help="score logged picks against final scores")
    pk.set_defaults(func=cmd_picks)
    sc = sub.add_parser("scan", help="Kalshi vs. sportsbook prices, no API keys needed")
    sc.add_argument("--sport", choices=["cfb", "nfl"], default="cfb")
    sc.add_argument("--days", type=int, default=1, help="today plus this many days (default 1)")
    sc.add_argument("--top", type=int, default=15, help="rows to show (default 15)")
    sc.add_argument("--all", action="store_true", help="show every side")
    sc.add_argument("--bankroll", type=float, default=1000.0)
    sc.set_defaults(func=cmd_scan)
    pp = sub.add_parser("props", help="NFL player-prop projections (and edges when lines are available)")
    pp.add_argument("--days", type=int, default=0)
    pp.add_argument("--bankroll", type=float, default=1000.0)
    pp.add_argument("--fresh", action="store_true", help="re-download all live data right now")
    pp.add_argument("--backtest", action="store_true", help="show the walk-forward validation instead")
    pp.set_defaults(func=cmd_props)
    p = sport_args(sub.add_parser("parlay", help="parlay fair price"))
    p.add_argument("legs", nargs="+", help="GAMEID:TEAM (2 or 3 legs)")
    p.add_argument("--offered", type=float, help="your book's parlay price in American odds, e.g. 450")
    p.set_defaults(func=cmd_parlay)
    s = sub.add_parser("serve", help="open the local web app")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    args = parser.parse_args()
    try:
        args.func(args)
    except (MissingKeyError, ApiError) as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
