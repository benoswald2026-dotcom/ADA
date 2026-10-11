"""College football model: command-line entry point.

    python main.py week                      current-week table
    python main.py backtest                  2023-2025 validation report
    python main.py parlay ID:TEAM ID:TEAM    parlay fair price (2 or 3 legs)
    python main.py serve                     local web app at http://127.0.0.1:8000

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


def cmd_week(_args):
    from week import build_week
    week, rows = build_week()
    print(f"\nWeek {week['week']} ({week['season_type']}) — percentages are for the HOME team.\n")
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
            pct(p["home_prob"]), pct(m["home_prob"] if m else None),
            kalshi, edge, "FLAG" if r["flag"] else "PASS",
        ])
        inj = f" (incl. injuries {p['injury_adj']:+.1f})" if p["injury_adj"] else ""
        sources.append([g["id"], r["sources"]["model"] + inj, r["sources"]["market"],
                        r["sources"]["kalshi"]])
    print(tabulate(table, headers=["ID", "Game", "Model spread", "Market spread", "Model win%",
                                   "Market win%", "Kalshi (home YES ask)", "Edge after fees", "Bet"]))
    print("\nSOURCES & TIMESTAMPS (UTC) for every number above:")
    print(tabulate(sources, headers=["ID", "Model spread & win% (CFBD)",
                                     "Market spread & win% (no-vig avg)", "Kalshi price"]))
    print("\nFLAG = edge after Kalshi fee >= 3 pts AND model within 10 pts of market. "
          "Edge is for the better Kalshi side. Not betting advice.")


def cmd_backtest(_args):
    from backtest import report, run_backtest
    print(report(run_backtest()))


def cmd_parlay(args):
    from week import ParlayInputError, build_week, price_parlay

    picks = []
    for leg in args.legs:
        if ":" not in leg:
            sys.exit(f"Bad leg '{leg}'. Use GAMEID:TEAM (IDs are in `python main.py week`).")
        picks.append(tuple(leg.split(":", 1)))
    if not 2 <= len(picks) <= 3:
        sys.exit("Give 2 or 3 legs, e.g. python main.py parlay 401234:Georgia 401235:Texas")
    _week, rows = build_week()
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
    sub.add_parser("week", help="current-week table").set_defaults(func=cmd_week)
    sub.add_parser("backtest", help="2023-2025 validation report").set_defaults(func=cmd_backtest)
    p = sub.add_parser("parlay", help="parlay fair price")
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
