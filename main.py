"""College football + NFL model: command-line entry point.

    python main.py week [--sport nfl]                 this week's (NFL: today's) table
    python main.py backtest [--sport nfl]             2023-2025 validation report
    python main.py bets [--sport nfl]                 ranked Kalshi bets, singles, parlays
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


def load_slate(sport, days=0):
    """Returns (title, notes, rows) for either sport."""
    if sport == "nfl":
        from nfl import build_slate
        info, rows = build_slate(days=days)
        return info["label"], info["notes"], rows
    from week import build_week
    week, rows = build_week()
    return f"Week {week['week']} ({week['season_type']})", [], rows


def cmd_week(args):
    title, notes, rows = load_slate(args.sport, args.days)
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
            inj += (f" | env {p['env_margin']:+.1f} pts (rest {e['rest']:+.0f}d, travel {e['tz']:+.0f}h)"
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
    title, notes, rows = load_slate(args.sport, args.days)
    b = find_bets(rows)
    print(f"\nBET FINDER — {title}")
    for n in notes:
        print(f"NOTE: {n}")
    print("Ranked by expected value, not win chance. Only sides where model and market are within 10 pts.")
    print("Check `python main.py backtest" + (" --sport nfl" if args.sport == "nfl" else "") +
          "` first: if the model loses to the closing line, treat these as noise.\n")
    print("KALSHI (FLAG = edge after fee >= 3 pts and gap <= 10):")
    if b["kalshi"]:
        print(tabulate([[k["matchup"], k["team"], pct(k["model"]), pct(k["market"]), f"{k['price']:.2f}",
                         f"{k['edge'] * 100:+.1f}", "FLAG" if k["flag"] else "PASS"] for k in b["kalshi"]],
                       headers=["Game", "Team", "Model", "Market", "YES ask", "Edge pts", "Bet"]))
    else:
        print("  No Kalshi prices available.")
    print("\nSPORTSBOOK SINGLES with positive model EV (at average book moneyline):")
    print(tabulate([[x["matchup"], x["team"], pct(x["model"]), pct(x["market"]), f"{x['decimal']:.2f}",
                     f"{x['ev']:+.3f}"] for x in b["book"]],
                   headers=["Game", "Team", "Model", "Market", "Decimal", "EV per $1"]) if b["book"] else "  None.")
    print("\nPARLAYS (different games only, legs assumed independent):")
    print(tabulate([[" + ".join(l["team"] for l in x["legs"]), pct(x["prob"]), pct(x["market_prob"]),
                     f"{x['decimal']:.2f}", f"{x['ev']:+.3f}"] for x in b["parlays"]],
                   headers=["Legs", "Model hit %", "Market hit %", "Decimal", "EV per $1"]) if b["parlays"] else "  None.")
    if b["excluded_gap"]:
        print("\nExcluded (model and market more than 10 pts apart; model likely missing info):")
        print("  " + "; ".join(b["excluded_gap"]))
    print("\nNot betting advice. This tool never places bets.")


def cmd_parlay(args):
    from week import ParlayInputError, build_week, price_parlay

    picks = []
    for leg in args.legs:
        if ":" not in leg:
            sys.exit(f"Bad leg '{leg}'. Use GAMEID:TEAM (IDs are in `python main.py week`).")
        picks.append(tuple(leg.split(":", 1)))
    if not 2 <= len(picks) <= 3:
        sys.exit("Give 2 or 3 legs, e.g. python main.py parlay 401234:Georgia 401235:Texas")
    _title, _notes, rows = load_slate(args.sport, args.days)
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
    from server import serve
    serve(args.port)


def main():
    parser = argparse.ArgumentParser(description="College football model (read-only).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    def sport_args(sp, days=True):
        sp.add_argument("--sport", choices=["cfb", "nfl"], default="cfb")
        if days:
            sp.add_argument("--days", type=int, default=0, help="NFL: include the next N days too")
        return sp
    sport_args(sub.add_parser("week", help="current-week table")).set_defaults(func=cmd_week)
    sport_args(sub.add_parser("backtest", help="2023-2025 validation report"), days=False).set_defaults(func=cmd_backtest)
    sport_args(sub.add_parser("bets", help="ranked bets and parlays")).set_defaults(func=cmd_bets)
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
