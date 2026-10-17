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
import csv
import json
import math
import os
import re
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
# Shrinkage prior. Default: one league average for the stat. USAGE_PRIOR_STATS
# instead shrink toward the player's recent usage x the league rate per use
# (e.g. targets x yards per target). Checked on 2021-2022 (inside the fit years)
# and kept only where it cut the average miss with |t| >= 3: receiving yards
# (t = -4.3). It made passing yards worse (t = +4.7): hot QB stretches regress.
USAGE_PRIOR_STATS = {"rec_yds"}
# Multiplicative adjustments, each kept only if |t| >= 3 on the fit seasons.
# qb: the team's starting-QB gap (nfl.QBTracker, EPA per dropback): a backup
# starting should cut his receivers' yards beyond what the team total says.
TERMS = [("bias", None), ("opponent", "opp_x"), ("team_points", "team_x"), ("qb", "qb_x")]
# Weight of games played for a previous team. Discounting them (0.5) made
# rushing, receptions and receiving yards worse on 2021-2022, so it is off.
OLD_TEAM_WEIGHT = 1.0
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
        self.stat, self.s = stat, STATS[stat]
        self.player = defaultdict(list)       # player_id -> [(value, usage, team)]
        self.rate_v, self.rate_u = [], []     # qualifying player-games: value, usage (league rate)
        self.allowed = defaultdict(list)      # defense team -> [total allowed per game]
        self.league_vals = []                 # qualifying player-game values
        self.league_allowed = []              # per-defense-game totals

    def prior(self):
        return float(np.mean(self.league_vals[-4000:])) if self.league_vals else 0.0

    def rate(self):
        """League value per unit of usage (e.g. yards per target), recent qualifying games."""
        u = sum(self.rate_u[-4000:])
        return sum(self.rate_v[-4000:]) / u if u > 0 else 0.0

    def baseline(self, pid, team=None):
        """(projection baseline, effective games, avg recent usage).
        Recent games count more (HALF_LIFE); games for a team other than `team`
        count OLD_TEAM_WEIGHT as much. The average is shrunk SHRINK_GAMES
        pseudo-games toward a prior: the player's recent usage times the league
        rate per use for USAGE_PRIOR_STATS, so a 9-target WR1 is shrunk toward a WR1."""
        hist = self.player.get(pid, [])
        if not hist:
            return self.prior(), 0.0, 0.0
        recent = hist[-16:]
        vals = np.array([v for v, _, _ in recent])
        w = 0.5 ** (np.arange(len(vals))[::-1] / HALF_LIFE)
        if team is not None:
            w = w * np.array([1.0 if t == team else OLD_TEAM_WEIGHT for _, _, t in recent])
        usage = float(np.mean([u for _, u, _ in hist[-3:]]))
        use_usage = self.stat in USAGE_PRIOR_STATS and usage > 0 and self.rate() > 0
        prior = usage * self.rate() if use_usage else self.prior()
        base = (np.sum(w * vals) + SHRINK_GAMES * prior) / (np.sum(w) + SHRINK_GAMES)
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
                self.player[r["player_id"]].append((v, u, r.get("team")))
                if u >= self.s["min_usage"]:
                    self.league_vals.append(v)
                    self.rate_v.append(v)
                    self.rate_u.append(u)
        for (_, team), tot in by_def.items():
            self.allowed[team].append(tot)
            self.league_allowed.append(tot)


def build_examples(stat, df, games, qb_gaps=None):
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
            base, n_eff, usage = h.baseline(r["player_id"], r["team"])
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
                        "qb_x": (qb_gaps or {}).get((r["game_id"], r["team"]), 0.0),
                        "actual": float(r[s["col"]]), "naive": base})
        h.add_game(recs)
    return out


def fit(examples, seasons):
    """Fit the adjustments (t >= 3 gate) and the outcome spread on `seasons`."""
    ex = [e for e in examples if e["season"] in seasons and e["base"] > 0]
    y = np.array([e["actual"] / e["base"] - 1.0 for e in ex])
    X = np.column_stack([np.ones(len(ex))] + [[e.get(key, 0.0) for e in ex] for _, key in TERMS[1:]])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    cov = (resid @ resid / max(len(y) - X.shape[1], 1)) * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    names = [n for n, _ in TERMS]
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
    for name, key in TERMS:
        if name in t and t[name]["accepted"]:
            mult += t[name]["coef"] * (1.0 if key is None else e.get(key, 0.0))
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


def evaluate(stat, df, games, test_seasons=None, qb_gaps=None):
    """Walk-forward test: each test season fitted on FIT_START..season-1."""
    test_seasons = test_seasons or config.NFL_TEST_SEASONS
    ex = build_examples(stat, df, games, qb_gaps)
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
    from nfl import load_games, qb_gap_table
    games, stamp = load_games()
    last = config.NFL_TEST_SEASONS[-1]
    df = load_player_weeks(last, last + 1)
    qb_gaps = qb_gap_table(games, last)
    return {"stats": [evaluate(s, df, games, qb_gaps=qb_gaps) for s in STATS], "source": f"nflverse player stats + schedules @ {stamp}"}


# ------------------------------------------------------------ kalshi --------

KALSHI_PROP_SERIES = {"pass_yds": "KXNFLPASSYDS", "rush_yds": "KXNFLRSHYDS",
                      "receptions": "KXNFLREC", "rec_yds": "KXNFLRECYDS"}
PROPS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "props_log.csv")
PROPS_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "props_weights.json")
PROPS_LOG_FIELDS = ["logged_at", "season", "game_id", "kickoff", "player_id", "player", "team", "stat", "strike",
                    "side", "ask", "mid", "p_model", "p_used", "edge", "flag"]
PRIOR_PICKS = 40           # learned trust starts at 1.0 and moves only as graded props pile up
_KALSHI_NAME = re.compile(r"^(.*):\s*(\d+)\+")


def player_key(name):
    """Comparable player name: lower case, no punctuation, no Jr./Sr./II/III."""
    clean = (name or "").replace(".", "").replace("'", "").replace("\u2019", "")
    words = [w for w in normalize(clean).split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return " ".join(words)


def kalshi_props():
    """Kalshi NFL player-prop ladders: {(stat, player key): {"name", "event", "segment", "strikes": [...]}}.
    Each strike is a 'Player: N+' contract (pays if the player gets N or more)."""
    from api_clients import kalshi_markets
    out = {}
    for stat, series in KALSHI_PROP_SERIES.items():
        try:
            ms, _ = kalshi_markets(series)
        except ApiError:
            continue
        for m in ms:
            mm = _KALSHI_NAME.match(m.get("team") or "")
            if not mm:
                continue
            name, n = mm.group(1).strip(), int(mm.group(2))
            parts = (m.get("ticker") or "").split("-")
            entry = out.setdefault((stat, player_key(name)), {"name": name, "event": m.get("event_ticker", ""),
                                                              "segment": parts[2] if len(parts) > 2 else "", "strikes": []})
            entry["strikes"].append({"n": n, "yes_ask": m.get("yes_ask"), "yes_bid": m.get("yes_bid"),
                                     "no_ask": m.get("no_ask"), "ticker": m.get("ticker")})
    for e in out.values():
        e["strikes"].sort(key=lambda x: x["n"])
    return out


def _mid(k):
    a, b = k.get("yes_ask"), k.get("yes_bid")
    if a is None or not 0 < a < 1:
        return None
    return (a + b) / 2 if b is not None and 0 < b < a else a


def kalshi_median(strikes):
    """Where the ladder's midpoints cross 50%: Kalshi's own line for the player."""
    pts = [(k["n"], m) for k in strikes if (m := _mid(k)) is not None]
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return x0 + (p0 - 0.5) * (x1 - x0) / (p0 - p1)
    return None


def load_props_trust(path=None):
    try:
        with open(path or PROPS_WEIGHTS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def best_kalshi_prop(strikes, pj, sd, fitted, trust=1.0):
    """Best Kalshi contract for one player-stat. For each 'N+' strike priced 20-80c,
    P(YES) = P(stat >= N) from the model's calibrated distribution, shrunk toward
    Kalshi's own price by the learned trust. Returns the best YES/NO side, or None."""
    from kalshi import edge_after_fee
    best = None
    for k in strikes:
        mid = _mid(k)
        if mid is None:
            continue
        p_model = p_over(k["n"] - 0.5, pj, sd, fitted)          # N+ means at least N
        p_used = mid + trust * (p_model - mid)
        for side, p, pm, ask in (("YES", p_used, p_model, k.get("yes_ask")), ("NO", 1 - p_used, 1 - p_model, k.get("no_ask"))):
            if ask is None or not 0.2 <= ask <= 0.8:
                continue
            edge = edge_after_fee(p, ask)
            m_side = mid if side == "YES" else 1 - mid
            cand = {"n": k["n"], "side": side, "ask": ask, "mid": mid, "p_model": pm, "p_used": p, "edge": edge,
                    "gap_ok": abs(pm - m_side) <= config.MAX_MODEL_MARKET_GAP, "ticker": k.get("ticker")}
            if best is None or edge > best["edge"]:
                best = cand
    return best


def espn_injuries(rows):
    """{team code: {player key: status}} from ESPN's game summaries for this slate."""
    from scan import espn_events
    out, days = {}, sorted({str(r["game"].get("start") or "")[:10].replace("-", "") for r in rows if r["game"].get("start")})
    for day in days:
        try:
            events, _ = espn_events("nfl", day)
        except ApiError:
            continue
        for e in events:
            try:
                sm, _ = cached_get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
                                   params={"event": e["id"]}, ttl=config.TTL_ODDS, source="ESPN summary")
            except ApiError:
                continue
            for t in sm.get("injuries", []):
                code = T.code_for(t.get("team", {}).get("displayName", ""))
                if code:
                    out.setdefault(code, {}).update({player_key(x.get("athlete", {}).get("displayName", "")):
                                                     str(x.get("status", "")).lower() for x in t.get("injuries", [])})
    return out


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


def _starting_qb_only(usage, starter, team, df):
    """QB rows follow the listed starter (nflverse schedule): drop other QBs, and
    add the starter if his recent usage was too low to qualify (a backup getting
    the start). Non-QB rows are untouched. No listed starter -> unchanged."""
    if not starter:
        return usage
    keep = [k for k in usage.index if k[2] != "QB" or k[1] == starter]
    usage = usage.loc[keep] if keep else usage.iloc[0:0]
    if any(k[2] == "QB" for k in usage.index) or "QB" not in set(df["position"]):
        return usage
    me = df[(df["player_display_name"] == starter) & (df["position"] == "QB")]
    if me.empty:
        return usage
    on_team = me[me["team"] == team]
    pid = (on_team if not on_team.empty else me).iloc[-1]["player_id"]
    extra = pd.Series([0.0], index=pd.MultiIndex.from_tuples([(pid, starter, "QB")], names=usage.index.names))
    return pd.concat([usage, extra]) if len(usage) else extra


def build_props(rows, bankroll=1000.0):
    """Projections (and prop bets when lines are available) for a slate from nfl.build_slate."""
    from nfl import load_games
    games, stamp = load_games()
    season = max(int(str(r["game"]["id"])[:4]) for r in rows) if rows else datetime.now().year
    df = load_player_weeks(season, season)
    notes, out = [], []
    if season not in _FIT_CACHE:   # the fit takes ~30 s; do it once per run
        from nfl import qb_gap_table
        qb_gaps = qb_gap_table(games, season)
        _FIT_CACHE[season] = {stat: fit(build_examples(stat, df, games, qb_gaps), list(range(FIT_START, season)))
                              for stat in STATS}
    fits = _FIT_CACHE[season]

    # Kalshi prop ladders (no key needed), ESPN injury reports, and the learned trust.
    try:
        kal = kalshi_props()
    except Exception as e:      # a Kalshi outage must not sink the projections
        kal = {}
        notes.append(f"Kalshi props unavailable ({e}).")
    if not kal:
        notes.append("No Kalshi player-prop markets found right now; projections only.")
    try:
        inj = espn_injuries(rows)
    except Exception:
        inj = {}
    trust = load_props_trust().get("trust", 1.0)
    latest = df.sort_values(["season", "week"]).groupby("player_id").tail(1)
    team_of = {player_key(n): t for n, t in zip(latest["player_display_name"], latest["team"])}

    def kalshi_qb(team, game_codes):
        """The QB Kalshi lists passing-yard props for on this team (the market's starter)."""
        names = set()
        for (st, _), e in kal.items():
            if st != "pass_yds" or not all(c in e["event"] for c in game_codes):
                continue
            t = team_of.get(player_key(e["name"]))
            if t == team or (t is None and e["segment"].startswith(team)):
                names.add(e["name"])
        return names.pop() if len(names) == 1 else None

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
                listed = (r.get("env") or {}).get(f"{side}_qb")
                market_qb = kalshi_qb(team, (g["home_code"], g["away_code"])) if s["pos"] & {"QB"} else None
                if market_qb and listed and player_key(market_qb) != player_key(listed):
                    # Follow Kalshi's QB only when ESPN says the scheduled starter won't play;
                    # otherwise the two disagree with no injury news, so keep the schedule and say so.
                    starter_status = inj.get(team, {}).get(player_key(listed), "")
                    if starter_status in ("out", "injured reserve", "doubtful"):
                        if stat == "pass_yds":
                            notes.append(f"{team}: {listed} is {starter_status} (ESPN); using {market_qb}, the QB Kalshi lists.")
                        listed = market_qb
                    elif stat == "pass_yds":
                        notes.append(f"{team}: schedule lists {listed} at QB but Kalshi has props for {market_qb} "
                                     "(no injury listed). Check the news before betting this team's props.")
                usage = _starting_qb_only(usage, listed, team, df)
                out_now = {k for k, st in inj.get(team, {}).items() if st in ("out", "injured reserve")}
                dropped = [k[1] for k in usage.index if player_key(k[1]) in out_now]
                if dropped:
                    usage = usage.loc[[k for k in usage.index if player_key(k[1]) not in out_now]]
                    if stat == "rec_yds" or stat == "pass_yds" or stat == "rush_yds":
                        notes.append(f"{team}: left out (ESPN lists Out): {', '.join(dropped)}.")
                tp = implied_team_points(m.get("total"), m.get("home_spread"), side == "home")
                for (pid, name, pos), u in usage.items():
                    base, n_eff, _ = h.baseline(pid, team)
                    e = {"base": base, "opp_x": h.opp_ratio(opp) - 1.0, "team_x": (tp / LEAGUE_TEAM_POINTS - 1.0) if tp else 0.0,
                         "qb_x": (r.get("env") or {}).get(f"{side}_qb_gap", 0.0)}
                    pj = project(e, f)
                    sd = sd_for(pj, f)
                    row = {"game_id": g["id"], "matchup": f"{g['away']} @ {g['home']}", "start": g.get("start"),
                           "team": team, "player": name, "position": pos, "stat": stat, "label": s["label"],
                           "projection": pj, "sd": sd, "q25": max(0.0, quantile(0.25, pj, sd, f)),
                           "median": quantile(0.5, pj, sd, f), "q75": quantile(0.75, pj, sd, f),
                           "fair_line": math.floor(quantile(0.5, pj, sd, f)) + 0.5,
                           "team_points": tp, "opp_factor": 1 + e["opp_x"], "market": None, "stake": 0.0,
                           "player_id": pid, "season": season,
                           "injury": inj.get(team, {}).get(player_key(name))}
                    kq = kal.get((stat, player_key(name)))
                    if kq:
                        bk = best_kalshi_prop(kq["strikes"], pj, sd, f, trust)
                        row["kalshi_median"] = kalshi_median(kq["strikes"])
                        if bk:
                            from strategy import kelly_fraction_kalshi
                            from kalshi import kalshi_fee
                            label = f"{kq['name']}: {bk['n']}+"
                            row.update({"kalshi": {**bk, "contract": label}, "pick": f"{bk['side']} {label}",
                                        "model": bk["p_used"], "ev": bk["edge"] / (bk["ask"] + kalshi_fee(bk["ask"])),
                                        "decimal": 1 / (bk["ask"] + kalshi_fee(bk["ask"])), "gap_ok": bk["gap_ok"],
                                        "flag": bk["edge"] >= config.MIN_EDGE and bk["gap_ok"],
                                        "kelly": kelly_fraction_kalshi(bk["p_used"], bk["ask"]), "venue": "kalshi"})
                    q = book.get(normalize(name))
                    if q and not row.get("kalshi"):
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
    notes = list(dict.fromkeys(notes))     # the same note can come from several stats
    return {"rows": out, "notes": notes, "fits": fit_summary, "source": f"nflverse player stats @ {stamp}",
            "validated": False, "total_stake": round(sum(x["stake"] for x in staked), 2), "trust": trust,
            "kalshi_players": sum(1 for x in out if x.get("kalshi"))}


def log_props(result, path=None):
    """Save each Kalshi-priced player-stat (its best contract, flagged or not) before kickoff,
    so results can be graded and the trust factor learned. Reruns before kickoff replace the row."""
    path = path or PROPS_LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now().astimezone()
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    index = {(r["game_id"], r["player_id"], r["stat"]): i for i, r in enumerate(rows)}
    for x in result["rows"]:
        k = x.get("kalshi")
        if not k or not x.get("start"):
            continue
        kick = datetime.fromisoformat(str(x["start"]))
        if kick <= now:
            continue
        row = {"logged_at": now.strftime("%Y-%m-%d %H:%M%z"), "season": x["season"], "game_id": x["game_id"],
               "kickoff": x["start"], "player_id": x["player_id"], "player": x["player"], "team": x["team"],
               "stat": x["stat"], "strike": k["n"], "side": k["side"], "ask": k["ask"], "mid": round(k["mid"], 4),
               "p_model": round(k["p_model"], 4), "p_used": round(k["p_used"], 4), "edge": round(k["edge"], 4),
               "flag": int(bool(x.get("flag")))}
        key = (str(x["game_id"]), str(x["player_id"]), x["stat"])
        if key in index:
            rows[index[key]] = row
        else:
            index[key] = len(rows)
            rows.append(row)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROPS_LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)


def learn_props(path=None, weights_path=None):
    """Grade logged props against nflverse box scores and learn the trust factor:
    the slope of (result - Kalshi price) on (model - Kalshi price), shrunk toward 1.0
    with PRIOR_PICKS and kept in [0, 1.2]. Also keeps the FLAG record. Saves and returns it."""
    path, weights_path = path or PROPS_LOG, weights_path or PROPS_WEIGHTS
    out = load_props_trust(weights_path) or {"trust": 1.0, "graded": 0}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as f:
        logged = list(csv.DictReader(f))
    if not logged:
        return out
    seasons = sorted({int(r["season"]) for r in logged})
    try:
        df = load_player_weeks(seasons[-1], seasons[-1])
    except ApiError:
        return out
    actual = {}
    for stat, sdef in STATS.items():
        for gid, pid, v in zip(df["game_id"], df["player_id"], df[sdef["col"]]):
            actual[(str(gid), str(pid), stat)] = float(v)
    sxy = sxx = 0.0
    n, rec = 0, {"W": 0, "L": 0}
    graded = []
    for r in logged:
        v = actual.get((r["game_id"], r["player_id"], r["stat"]))
        if v is None:
            continue
        y_yes = 1.0 if v >= float(r["strike"]) else 0.0
        mid = float(r["mid"])
        p_yes = float(r["p_model"]) if r["side"] == "YES" else 1 - float(r["p_model"])
        sxy += (p_yes - mid) * (y_yes - mid)
        sxx += (p_yes - mid) ** 2
        n += 1
        won = (y_yes == 1.0) == (r["side"] == "YES")
        graded.append({**r, "actual": v, "result": "W" if won else "L"})
        if r["flag"] == "1":
            rec["W" if won else "L"] += 1
    slope = sxy / sxx if sxx > 0 else 1.0
    trust = (n * slope + PRIOR_PICKS) / (n + PRIOR_PICKS)
    out = {"trust": round(min(1.2, max(0.0, trust)), 4), "graded": n, "raw_slope": round(slope, 3),
           "flag_record": rec, "updated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M%z")}
    os.makedirs(os.path.dirname(weights_path), exist_ok=True)
    with open(weights_path, "w") as f:
        json.dump(out, f, indent=1)
    out["rows"] = graded
    return out


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
