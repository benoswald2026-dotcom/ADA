"""NFL player props: passing yards, rushing yards, receptions, receiving yards.

Method (same research rules as the game model, see data/research.json):
  1. Player baseline: exponentially weighted average of the player's earlier
     games (recent games count more), shrunk toward the league average when
     the player has few games.
  2. Two pre-registered adjustments, each used only if |t| >= 3 on the fit
     seasons (harvey-liu-zhu-2016):
       - opponent: how much of this stat the defense has allowed vs average
       - team context: the team's expected points from the spread and total
  3. Outcome spread: normal distribution, sd = s0 + s1 * projection, fitted
     on the fit seasons. This turns a projection into P(over the line).
  4. Walk-forward test on 2023-2025 (mclean-pontiff-2016): projection error
     vs simple baselines, and whether the predicted quartiles are honest.

What cannot be tested: there is no free history of prop LINES, so the model
can't be backtested against the prop market. Prop edges are labelled
"unvalidated" until paper trades build a record.

Read-only: nothing here places a bet.
"""
import math
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

import config
import nfl_teams as T
from cache import ApiError, cached_download, cached_get
from names import normalize
from strategy import T_STAT_BAR, kelly_fraction_decimal, normal_cdf, size_bets

PLAYER_WEEK_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
FIRST_SEASON = 2018            # warm-up for player histories
FIT_START = 2019
HALF_LIFE = 4.0                # games
SHRINK_GAMES = 3.0             # pseudo-games of league average
OPP_SHRINK_GAMES = 4.0
LEAGUE_TEAM_POINTS = 22.5
ZQ_LEVELS = np.linspace(0, 1, 201)      # average NFL team score, to scale the team-points adjustment

STATS = {
    "pass_yds": {"label": "Passing yards", "col": "passing_yards", "pos": {"QB"}, "usage": "attempts",
                 "min_usage": 15, "per_team": 1, "odds_market": "player_pass_yds", "discrete": False},
    "rush_yds": {"label": "Rushing yards", "col": "rushing_yards", "pos": {"RB", "QB"}, "usage": "carries",
                 "min_usage": 5, "per_team": 2, "odds_market": "player_rush_yds", "discrete": False},
    "receptions": {"label": "Receptions", "col": "receptions", "pos": {"WR", "TE", "RB"}, "usage": "targets",
                   "min_usage": 3, "per_team": 4, "odds_market": "player_receptions", "discrete": True},
    "rec_yds": {"label": "Receiving yards", "col": "receiving_yards", "pos": {"WR", "TE", "RB"}, "usage": "targets",
                "min_usage": 3, "per_team": 4, "odds_market": "player_reception_yds", "discrete": False},
}
KEEP = ["player_id", "player_display_name", "position", "season", "week", "season_type", "game_id", "team",
        "opponent_team", "attempts", "carries", "targets", "passing_yards", "rushing_yards", "receptions",
        "receiving_yards"]


# ------------------------------------------------------------------ data --

def load_player_weeks(last_season, current_season):
    frames = []
    for s in range(FIRST_SEASON, last_season + 1):
        ttl = 3600 if s >= current_season else None
        try:
            path, _ = cached_download(PLAYER_WEEK_URL.format(season=s), ttl=ttl, source="nflverse player stats")
        except ApiError:
            if s >= current_season:
                continue
            raise
        df = pd.read_csv(path, usecols=lambda c: c in KEEP, low_memory=False)
        frames.append(df[df["season_type"].isin(["REG", "POST"])])
    df = pd.concat(frames, ignore_index=True)
    for c in ("attempts", "carries", "targets", "passing_yards", "rushing_yards", "receptions", "receiving_yards"):
        df[c] = df[c].fillna(0.0)
    df["team"] = df["team"].map(T.canon)
    df["opponent_team"] = df["opponent_team"].map(T.canon)
    return df.sort_values(["season", "week"]).reset_index(drop=True)


def implied_team_points(total, home_spread, is_home):
    """Expected points for one team from the market (home_spread: -3 = home favored by 3)."""
    if total is None or home_spread is None or any(isinstance(x, float) and math.isnan(x) for x in (total, home_spread)):
        return None
    return total / 2 - home_spread / 2 if is_home else total / 2 + home_spread / 2


# ------------------------------------------------------ walk-forward ------

class History:
    """Running, leak-free state: each game is predicted before it is added."""

    def __init__(self, stat):
        self.s = STATS[stat]
        self.player = defaultdict(list)       # player_id -> [(value, usage)]
        self.allowed = defaultdict(list)      # defense team -> [total allowed per game]
        self.league_vals = []                 # qualifying player-game values
        self.league_allowed = []              # per-defense-game totals

    def prior(self):
        return float(np.mean(self.league_vals[-4000:])) if self.league_vals else 0.0

    def baseline(self, pid):
        """(projection baseline, effective games, avg recent usage)."""
        hist = self.player.get(pid, [])
        if not hist:
            return self.prior(), 0.0, 0.0
        vals = np.array([v for v, _ in hist[-16:]])
        w = 0.5 ** (np.arange(len(vals))[::-1] / HALF_LIFE)
        base = (np.sum(w * vals) + SHRINK_GAMES * self.prior()) / (np.sum(w) + SHRINK_GAMES)
        usage = float(np.mean([u for _, u in hist[-3:]]))
        return float(base), float(np.sum(w)), usage

    def opp_ratio(self, team):
        hist = self.allowed.get(team, [])
        league = float(np.mean(self.league_allowed[-2000:])) if self.league_allowed else 0.0
        if not hist or league <= 0:
            return 1.0
        vals = np.array(hist[-10:])
        w = 0.5 ** (np.arange(len(vals))[::-1] / HALF_LIFE)
        shrunk = (np.sum(w * vals) + OPP_SHRINK_GAMES * league) / (np.sum(w) + OPP_SHRINK_GAMES)
        return float(shrunk / league)

    def add_game(self, rows):
        """rows: this game's player rows (one team-game or a whole week)."""
        by_def = defaultdict(float)
        for r in rows:
            v, u = float(r[self.s["col"]]), float(r[self.s["usage"]])
            if r["position"] in self.s["pos"]:
                by_def[(r["game_id"], r["opponent_team"])] += v
                # Every game counts toward the player's history (keeping only
                # high-usage games would bias his baseline upward).
                self.player[r["player_id"]].append((v, u))
                if u >= self.s["min_usage"]:
                    self.league_vals.append(v)
        for (_, team), tot in by_def.items():
            self.allowed[team].append(tot)
            self.league_allowed.append(tot)


def build_examples(stat, df, games):
    """Every qualifying player-game with features computed from earlier games only.
    A player qualifies if, BEFORE the game, he averaged >= min_usage over his last
    3 games and has at least 2 earlier games (no peeking at this game's usage)."""
    s = STATS[stat]
    h = History(stat)
    sched = games.set_index("game_id")
    out = []
    for (season, week), wk in df.groupby(["season", "week"], sort=True):
        recs = wk.to_dict("records")
        for r in recs:
            if r["position"] not in s["pos"]:
                continue
            base, n_eff, usage = h.baseline(r["player_id"])
            if len(h.player.get(r["player_id"], [])) < 2 or usage < s["min_usage"]:
                continue
            tp = None
            if r["game_id"] in sched.index:
                g = sched.loc[r["game_id"]]
                is_home = T.canon(g["home_team"]) == r["team"]
                sl = g["spread_line"]
                tp = implied_team_points(g["total_line"], None if pd.isna(sl) else -sl, is_home)
            out.append({"season": season, "week": week, "player_id": r["player_id"], "player": r["player_display_name"],
                        "team": r["team"], "opp": r["opponent_team"], "base": base, "n_eff": n_eff,
                        "opp_x": h.opp_ratio(r["opponent_team"]) - 1.0,
                        "team_x": (tp / LEAGUE_TEAM_POINTS - 1.0) if tp else 0.0,
                        "actual": float(r[s["col"]]), "naive": base})
        h.add_game(recs)
    return out


def fit(examples, seasons):
    """Fit the adjustments (t >= 3 gate) and the outcome spread on `seasons`."""
    ex = [e for e in examples if e["season"] in seasons and e["base"] > 0]
    y = np.array([e["actual"] / e["base"] - 1.0 for e in ex])
    X = np.column_stack([np.ones(len(ex)), [e["opp_x"] for e in ex], [e["team_x"] for e in ex]])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    cov = (resid @ resid / max(len(y) - 3, 1)) * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    names = ["bias", "opponent", "team_points"]
    terms = {n: {"coef": float(b), "se": float(e), "t": float(b / e) if e > 0 else 0.0,
                 "accepted": bool(abs(b / e) >= T_STAT_BAR) if e > 0 else False}
             for n, b, e in zip(names, beta, se)}
    fitted = {"terms": terms, "games": len(ex), "seasons": [min(seasons), max(seasons)]}
    proj = np.array([project(e, fitted) for e in ex])
    act = np.array([e["actual"] for e in ex])
    # sd ~ s0 + s1 * projection, from mean absolute error (MAD * sqrt(pi/2) = sd for a normal)
    A = np.column_stack([np.ones(len(ex)), proj])
    s, *_ = np.linalg.lstsq(A, np.abs(act - proj) * math.sqrt(math.pi / 2), rcond=None)
    fitted["sd"] = [float(s[0]), float(s[1])]
    # Yards are skewed (a few huge games), so a normal curve would put too much
    # weight on overs. Use the empirical shape of past standardized misses instead.
    z = (act - proj) / np.array([sd_for(p, fitted) for p in proj])
    fitted["zq"] = [float(x) for x in np.quantile(z, ZQ_LEVELS)]
    return fitted


def project(e, fitted):
    t = fitted["terms"]
    mult = 1.0
    for name, key in (("bias", None), ("opponent", "opp_x"), ("team_points", "team_x")):
        if t[name]["accepted"]:
            mult += t[name]["coef"] * (1.0 if key is None else e[key])
    return max(0.0, e["base"] * mult)


def sd_for(proj, fitted):
    s0, s1 = fitted["sd"]
    return max(1.0, s0 + s1 * proj)


def z_cdf(z, fitted):
    """Empirical CDF of standardized misses (fitted on earlier seasons only)."""
    return float(np.interp(z, fitted["zq"], ZQ_LEVELS, left=0.0, right=1.0))


def p_over(line, proj, sd, fitted):
    """P(result > line). Lines are x.5, so for receptions 'over 4.5' means 5 or more."""
    return 1 - z_cdf((line - proj) / sd, fitted)


def quantile(q, proj, sd, fitted):
    return proj + sd * float(np.interp(q, ZQ_LEVELS, fitted["zq"]))


def evaluate(stat, df, games, test_seasons=None):
    """Walk-forward test: each test season fitted on FIT_START..season-1."""
    test_seasons = test_seasons or config.NFL_TEST_SEASONS
    ex = build_examples(stat, df, games)
    rows, fits, fits_by = [], [], {}
    for S in test_seasons:
        f = fit(ex, list(range(FIT_START, S)))
        fits_by[S] = f
        fits.append({"season": S, **{k: v for k, v in f.items() if k != "zq"}})
        for e in (e for e in ex if e["season"] == S):
            pj = project(e, f)
            sd = sd_for(pj, f)
            rows.append((e, pj, sd))
    act = np.array([e["actual"] for e, _, _ in rows])
    pj = np.array([p for _, p, _ in rows])
    sd = np.array([s for _, _, s in rows])
    naive = np.array([e["naive"] for e, _, _ in rows])
    cdf = np.array([z_cdf((a - p) / s_, f) for (e, p, s_), a, f in
                    zip(rows, act, [fits_by[e["season"]] for e, _, _ in rows])])
    cover = {q: float(np.mean(cdf <= q)) for q in (0.25, 0.5, 0.75)}
    return {"stat": stat, "label": STATS[stat]["label"], "n": len(rows),
            "mae_model": float(np.mean(np.abs(act - pj))), "mae_baseline": float(np.mean(np.abs(act - naive))),
            "bias": float(np.mean(act - pj)), "coverage": cover, "fits": fits}


def run_backtest():
    from nfl import load_games
    games, stamp = load_games()
    last = config.NFL_TEST_SEASONS[-1]
    df = load_player_weeks(last, last + 1)
    return {"stats": [evaluate(s, df, games) for s in STATS], "source": f"nflverse player stats + schedules @ {stamp}"}


# ------------------------------------------------------------ live --------

def _prop_odds(event_id, markets):
    params = {"apiKey": config.get_key("ODDS_API_KEY"), "regions": "us", "markets": ",".join(markets),
              "oddsFormat": "american"}
    return cached_get(f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds",
                      params=params, ttl=config.TTL_ODDS, source="The Odds API (props)")


def parse_prop_market(event, market_key):
    """{normalized player: {line, p_over (no-vig avg), dec_over, dec_under, books}} at each
    player's most common line across books."""
    from collections import Counter
    from odds import american_to_decimal, remove_vig
    quotes = defaultdict(list)    # player -> [(point, over_price, under_price)]
    for book in event.get("bookmakers", []):
        for m in book.get("markets", []):
            if m["key"] != market_key:
                continue
            pairs = defaultdict(dict)
            for o in m.get("outcomes", []):
                if o.get("point") is None or not o.get("description"):
                    continue
                pairs[(o["description"], o["point"])][o["name"]] = o["price"]
            for (player, point), sides in pairs.items():
                if "Over" in sides and "Under" in sides:
                    quotes[normalize(player)].append((float(point), sides["Over"], sides["Under"]))
    out = {}
    for player, qs in quotes.items():
        line = Counter(q[0] for q in qs).most_common(1)[0][0]
        at = [q for q in qs if q[0] == line]
        out[player] = {"line": line, "books": len(at),
                       "p_over": float(np.mean([remove_vig(o, u)[0] for _, o, u in at])),
                       "dec_over": float(np.mean([american_to_decimal(o) for _, o, _ in at])),
                       "dec_under": float(np.mean([american_to_decimal(u) for _, _, u in at]))}
    return out


_FIT_CACHE = {}


def build_props(rows, bankroll=1000.0):
    """Projections (and prop bets when lines are available) for a slate from nfl.build_slate."""
    from nfl import load_games
    games, stamp = load_games()
    season = max(int(str(r["game"]["id"])[:4]) for r in rows) if rows else datetime.now().year
    df = load_player_weeks(season, season)
    notes, out = [], []
    if season not in _FIT_CACHE:   # the fit takes ~30 s; do it once per run
        _FIT_CACHE[season] = {stat: fit(build_examples(stat, df, games), list(range(FIT_START, season)))
                              for stat in STATS}
    fits = _FIT_CACHE[season]

    # Live odds for props: one request per event (costs Odds API credits).
    event_ids, prop_lines = {}, {}
    try:
        from nfl import _odds_events
        events, _ = _odds_events()
        for e in events:
            h, a = T.code_for(e["home_team"]), T.code_for(e["away_team"])
            event_ids[frozenset((h, a))] = e["id"]
    except (config.MissingKeyError, ApiError) as e:
        notes.append(f"No prop prices: {str(e).rstrip('.')}. Showing projections and fair lines only.")

    for stat in STATS:
        s, f = STATS[stat], fits[stat]
        h = History(stat)
        cur = df[df["season"] == season]
        for _, wk in df.groupby(["season", "week"], sort=True):
            h.add_game(wk.to_dict("records"))
        recent = cur.sort_values("week").groupby("player_id").tail(3)
        for r in rows:
            g, m = r["game"], r.get("market") or {}
            ev_id = event_ids.get(frozenset((g["home_code"], g["away_code"])))
            if ev_id and ev_id not in prop_lines:
                try:
                    data, _ = _prop_odds(ev_id, [x["odds_market"] for x in STATS.values()])
                    prop_lines[ev_id] = data
                except ApiError as e:
                    prop_lines[ev_id] = None
                    notes.append(f"Prop odds failed for {g['away']} @ {g['home']}: {e}")
            book = parse_prop_market(prop_lines[ev_id], s["odds_market"]) if ev_id and prop_lines.get(ev_id) else {}
            for side in ("home", "away"):
                team = g[f"{side}_code"]
                opp = g["away_code" if side == "home" else "home_code"]
                pool = recent[(recent["team"] == team) & (recent["position"].isin(s["pos"]))]
                usage = pool.groupby(["player_id", "player_display_name", "position"])[s["usage"]].mean()
                usage = usage[usage >= s["min_usage"]].sort_values(ascending=False).head(s["per_team"])
                tp = implied_team_points(m.get("total"), m.get("home_spread"), side == "home")
                for (pid, name, pos), u in usage.items():
                    base, n_eff, _ = h.baseline(pid)
                    e = {"base": base, "opp_x": h.opp_ratio(opp) - 1.0, "team_x": (tp / LEAGUE_TEAM_POINTS - 1.0) if tp else 0.0}
                    pj = project(e, f)
                    sd = sd_for(pj, f)
                    row = {"game_id": g["id"], "matchup": f"{g['away']} @ {g['home']}", "start": g.get("start"),
                           "team": team, "player": name, "position": pos, "stat": stat, "label": s["label"],
                           "projection": pj, "sd": sd, "q25": max(0.0, quantile(0.25, pj, sd, f)),
                           "median": quantile(0.5, pj, sd, f), "q75": quantile(0.75, pj, sd, f),
                           "fair_line": math.floor(quantile(0.5, pj, sd, f)) + 0.5,
                           "team_points": tp, "opp_factor": 1 + e["opp_x"], "market": None, "stake": 0.0}
                    q = book.get(normalize(name))
                    if q:
                        po = p_over(q["line"], pj, sd, f)
                        ev_o, ev_u = po * q["dec_over"] - 1, (1 - po) * q["dec_under"] - 1
                        pick = "Over" if ev_o >= ev_u else "Under"
                        p_pick, dec, mkt = (po, q["dec_over"], q["p_over"]) if pick == "Over" else \
                            (1 - po, q["dec_under"], 1 - q["p_over"])
                        ev = max(ev_o, ev_u)
                        gap_ok = abs(p_pick - mkt) <= config.MAX_MODEL_MARKET_GAP
                        row.update({"market": q, "p_over": po, "pick": pick, "model": p_pick, "decimal": dec,
                                    "ev": ev, "gap_ok": gap_ok, "flag": ev >= config.MIN_EDGE and gap_ok,
                                    "kelly": kelly_fraction_decimal(p_pick, dec)})
                    out.append(row)
    staked = [x for x in out if x.get("flag")]
    for x in staked:   # size_bets needs these keys; props in one game share the per-game cap
        x.setdefault("side", x["pick"].lower())
    size_bets(staked, bankroll)
    out.sort(key=lambda x: (-(x.get("ev") or -9), x["matchup"], x["stat"], -x["projection"]))
    fit_summary = {k: {"terms": v["terms"], "games": v["games"], "seasons": v["seasons"]} for k, v in fits.items()}
    return {"rows": out, "notes": notes, "fits": fit_summary, "source": f"nflverse player stats @ {stamp}",
            "validated": False, "total_stake": round(sum(x["stake"] for x in staked), 2)}


def report(r):
    """Plain-text props validation report."""
    from tabulate import tabulate
    out = ["PLAYER PROPS VALIDATION (walk-forward 2023-2025). Source: " + r["source"],
           "No free history of prop LINES exists, so this checks projection accuracy and calibration, "
           "not profit against books.\n"]
    out.append(tabulate([[s["label"], s["n"], f"{s['mae_model']:.2f}", f"{s['mae_baseline']:.2f}", f"{s['bias']:+.2f}",
                          f"{s['coverage'][0.25]:.1%}", f"{s['coverage'][0.5]:.1%}", f"{s['coverage'][0.75]:.1%}"]
                         for s in r["stats"]],
                        headers=["Stat", "Player-games", "Avg miss (model)", "Avg miss (recent avg)", "Bias",
                                 "Below 25% pt", "Below median", "Below 75% pt"]))
    out.append("  Honest calibration = about 25% / 50% / 75% in the last three columns.\n")
    rows = []
    for s in r["stats"]:
        for name, t in s["fits"][-1]["terms"].items():
            rows.append([s["label"], name, f"{t['coef']:+.3f}", f"{t['t']:+.1f}", "USED" if t["accepted"] else "rejected"])
    out.append(tabulate(rows, headers=["Stat", "Adjustment", "Effect", "t", "Decision"]))
    return "\n".join(out)
