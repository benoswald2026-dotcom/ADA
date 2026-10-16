"""NFL model: Elo + opponent-adjusted EPA, plus rest, travel and weather.

Data:
  - nflverse (GitHub releases): schedules, scores, closing lines, rest days,
    roof, recorded wind/temperature, and weekly team EPA.
  - Open-Meteo: wind/temperature/rain forecast for today's outdoor games.
  - The Odds API (americanfootball_nfl) and Kalshi (KXNFLGAME): live prices.

No lookahead: games are replayed week by week. Each prediction uses only
earlier games. The rest/travel/weather effects are fitted on 2015-2022 ONLY
and then applied unchanged to the 2023-2025 test seasons and to live games.
"""
import csv
import os
from datetime import datetime, timedelta
from itertools import groupby
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
import injuries as inj
import nfl_teams as T
from backtest import ats_record, brier, calibration_bins, log_loss, paired_brier_diff, summarize, verdict
from cache import ApiError, _still_valid, cached_download, cached_get
from kalshi import edge_after_fee, should_flag
from odds import american_to_decimal, parse_odds_event, remove_vig
from ratings import RatingSystem, margin_to_prob

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = f"{NFLVERSE}/schedules/games.csv"
TEAM_WEEK_URL = NFLVERSE + "/stats_team/stats_team_week_{season}.csv"
KALSHI_NFL_SERIES = "KXNFLGAME"
ET = ZoneInfo("America/New_York")
OUTDOOR = {"outdoors", "open"}


# ------------------------------------------------------------------ data --

def load_games(ttl=3600):
    path, stamp = cached_download(GAMES_URL, ttl=ttl, source="nflverse schedules")
    g = pd.read_csv(path, low_memory=False)
    return g, stamp


def load_team_epa(season, current_season):
    """Per team-game offensive EPA/play from nflverse weekly team stats.
    Defensive EPA/play for a team = its opponent's offensive EPA/play."""
    ttl = 3600 if season >= current_season else None
    try:
        path, stamp = cached_download(TEAM_WEEK_URL.format(season=season), ttl=ttl,
                                      source="nflverse team stats")
    except ApiError:
        if season >= current_season:
            return {}, None   # no games yet this season
        raise
    w = pd.read_csv(path, low_memory=False).copy()
    plays = w["attempts"].fillna(0) + w["carries"].fillna(0) + w["sacks_suffered"].fillna(0)
    w["off_epa"] = (w["passing_epa"].fillna(0) + w["rushing_epa"].fillna(0)) / plays.where(plays > 0)
    by_game = {}
    for gid, grp in w.groupby("game_id"):
        if len(grp) != 2:
            continue
        a, b = grp.iloc[0], grp.iloc[1]
        rows = []
        for me, opp in ((a, b), (b, a)):
            if pd.notna(me["off_epa"]) and pd.notna(opp["off_epa"]):
                rows.append({"team": T.canon(me["team"]), "opponent": T.canon(opp["team"]),
                             "off_epa": float(me["off_epa"]), "def_epa": float(opp["off_epa"])})
        by_game[gid] = rows
    return by_game, stamp


PBP_URL = NFLVERSE + "/pbp/play_by_play_{season}.csv.gz"
PBP_COLS = ["game_id", "posteam", "defteam", "epa", "wp", "pass", "rush"]
# Pre-registered before any test (no tuning on 2023-2025):
PBP_WP_RANGE = (0.10, 0.90)   # drop garbage time: offense's win prob outside this range
PBP_EPA_CLIP = 4.0            # cap single-play EPA so one fluke play can't define a game


def pbp_game_rows(pbp):
    """Play-by-play DataFrame -> {game_id: [team-game EPA rows]}, the same shape
    as load_team_epa. Keeps pass and rush plays (sacks and scrambles count as
    passes in nflverse) with the offense's win prob inside PBP_WP_RANGE, and
    clips each play's EPA to +/-PBP_EPA_CLIP."""
    p = pbp[((pbp["pass"] == 1) | (pbp["rush"] == 1)) & pbp["epa"].notna() & pbp["posteam"].notna()
            & pbp["wp"].between(*PBP_WP_RANGE)].copy()
    p["epa"] = p["epa"].clip(-PBP_EPA_CLIP, PBP_EPA_CLIP)
    off = p.groupby(["game_id", "posteam", "defteam"])["epa"].mean().reset_index()
    by_game = {}
    for gid, grp in off.groupby("game_id"):
        if len(grp) != 2:
            continue
        a, b = grp.iloc[0], grp.iloc[1]
        by_game[gid] = [{"team": T.canon(me["posteam"]), "opponent": T.canon(opp["posteam"]),
                         "off_epa": float(me["epa"]), "def_epa": float(opp["epa"])}
                        for me, opp in ((a, b), (b, a))]
    return by_game


QB_COLS = ["game_id", "posteam", "id", "qb_dropback", "qb_epa"]


def pbp_qb_rows(pbp):
    """Play-by-play -> one row per QB per game: dropbacks and summed qb_epa
    (EPA credited to the QB: passes, sacks and scrambles), each play clipped
    to +/-PBP_EPA_CLIP. All dropbacks count; no garbage-time filter."""
    p = pbp[(pbp["qb_dropback"] == 1) & pbp["qb_epa"].notna() & pbp["id"].notna()].copy()
    p["qb_epa"] = p["qb_epa"].clip(-PBP_EPA_CLIP, PBP_EPA_CLIP)
    g = p.groupby(["game_id", "posteam", "id"])["qb_epa"].agg(["count", "sum"]).reset_index()
    return [{"game_id": r["game_id"], "team": T.canon(r["posteam"]), "qb_id": r["id"],
             "dropbacks": int(r["count"]), "epa": float(r["sum"])} for _, r in g.iterrows()]


def _load_pbp(season, current_season):
    """Download one season of nflverse play-by-play once and reduce it to two
    small cached CSVs: team-game EPA (pbp_game_rows) and QB-game EPA
    (pbp_qb_rows). The ~20 MB raw file is then deleted. Finished seasons are
    kept forever; the current season refreshes hourly. Returns (agg paths, stamp)."""
    ttl = 3600 if season >= current_season else None
    team_csv = os.path.join(config.CACHE_DIR, f"pbp_epa_{season}.csv")
    qb_csv = os.path.join(config.CACHE_DIR, f"pbp_qb_{season}.csv")
    meta = os.path.join(config.CACHE_DIR, f"pbp_{season}.stamp")
    if all(os.path.exists(x) for x in (team_csv, qb_csv, meta)):
        stamp, epoch = open(meta).read().split("|")
        if _still_valid(ttl, datetime.now().timestamp() - float(epoch)):
            return (team_csv, qb_csv), stamp
    try:
        path, stamp = cached_download(PBP_URL.format(season=season), ttl=0, source="nflverse play-by-play",
                                      suffix=".csv.gz")
    except ApiError:
        if season >= current_season:
            return None, None   # no games yet this season
        raise
    pbp = pd.read_csv(path, usecols=sorted(set(PBP_COLS) | set(QB_COLS)), low_memory=False)
    os.remove(path)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    by_game = pbp_game_rows(pbp)
    pd.DataFrame([{"game_id": g, **r} for g, rows in by_game.items() for r in rows],
                 columns=["game_id", "team", "opponent", "off_epa", "def_epa"]).to_csv(team_csv, index=False)
    pd.DataFrame(pbp_qb_rows(pbp), columns=["game_id", "team", "qb_id", "dropbacks", "epa"]).to_csv(qb_csv, index=False)
    with open(meta, "w") as f:
        f.write(f"{stamp}|{datetime.now().timestamp()}")
    return (team_csv, qb_csv), stamp


def load_pbp_epa(season, current_season):
    """Garbage-time-filtered EPA per team-game, same shape as load_team_epa."""
    paths, stamp = _load_pbp(season, current_season)
    if paths is None:
        return {}, None
    by_game = {}
    for r in pd.read_csv(paths[0]).to_dict("records"):
        by_game.setdefault(r["game_id"], []).append(
            {"team": r["team"], "opponent": r["opponent"], "off_epa": r["off_epa"], "def_epa": r["def_epa"]})
    return by_game, stamp


def load_qb_games(season, current_season):
    """{game_id: [QB-game rows]} for one season (see pbp_qb_rows)."""
    paths, _ = _load_pbp(season, current_season)
    if paths is None:
        return {}
    out = {}
    for r in pd.read_csv(paths[1]).to_dict("records"):
        out.setdefault(r["game_id"], []).append(r)
    return out


def season_epa(season, current_season):
    """EPA rows for one season from the configured source (config.NFL_EPA_SOURCE)."""
    if config.NFL_EPA_SOURCE == "pbp":
        return load_pbp_epa(season, current_season)
    return load_team_epa(season, current_season)


def _num(x):
    return None if pd.isna(x) else float(x)


def game_record(r):
    """One nflverse schedule row -> plain dict."""
    neutral = str(r.get("location", "")) == "Neutral"
    roof = None if pd.isna(r.get("roof")) else str(r["roof"])
    return {
        "id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
        "game_type": r["game_type"], "gameday": r["gameday"],
        "gametime": None if pd.isna(r.get("gametime")) else str(r["gametime"]),
        "home_raw": r["home_team"], "away_raw": r["away_team"],
        "home": T.canon(r["home_team"]), "away": T.canon(r["away_team"]),
        "home_pts": _num(r["home_score"]), "away_pts": _num(r["away_score"]),
        "completed": pd.notna(r["home_score"]) and pd.notna(r["away_score"]),
        "neutral": neutral, "home_fbs": True, "away_fbs": True,
        "home_rest": _num(r.get("home_rest")), "away_rest": _num(r.get("away_rest")),
        "roof": roof, "temp": _num(r.get("temp")), "wind": _num(r.get("wind")),
        "spread_line": _num(r.get("spread_line")), "total_line": _num(r.get("total_line")),
        "home_ml": _num(r.get("home_moneyline")), "away_ml": _num(r.get("away_moneyline")),
        "home_spread_odds": _num(r.get("home_spread_odds")), "away_spread_odds": _num(r.get("away_spread_odds")),
        "stadium": None if pd.isna(r.get("stadium")) else str(r["stadium"]),
        "home_qb_id": None if pd.isna(r.get("home_qb_id")) else str(r["home_qb_id"]),
        "away_qb_id": None if pd.isna(r.get("away_qb_id")) else str(r["away_qb_id"]),
        "home_qb": None if pd.isna(r.get("home_qb_name")) else str(r["home_qb_name"]),
        "away_qb": None if pd.isna(r.get("away_qb_name")) else str(r["away_qb_name"]),
    }


# ------------------------------------------------------- environment -----

def features(g, wind=None, temp=None):
    """Rest, travel and weather features for one game (home team's view).

    rest:  home rest days minus away rest days, capped at +/-7
    tz:    hours the AWAY team traveled east (west-coast team at an east-coast
           game = +3). 0 at neutral sites, where both teams travel.
    wind:  mph above the threshold, outdoor stadiums only
    cold:  1 if outdoor and 32F or colder
    """
    rest = 0.0
    if g["home_rest"] is not None and g["away_rest"] is not None:
        rest = float(np.clip(g["home_rest"] - g["away_rest"], -7, 7))
    tz = 0.0 if g["neutral"] else float(T.west_hours(g["away_raw"]) - T.west_hours(g["home_raw"]))
    outdoor = g["roof"] in OUTDOOR
    wind = g["wind"] if wind is None else wind
    temp = g["temp"] if temp is None else temp
    wind_x = max(0.0, wind - config.WIND_THRESHOLD_MPH) if outdoor and wind is not None else 0.0
    cold = 1.0 if outdoor and temp is not None and temp <= 32 else 0.0
    return {"rest": rest, "tz": tz, "wind_x": wind_x, "cold": cold}


def _ols(X, y):
    """Least squares with standard errors. Returns (coefs, std errors)."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(len(y) - X.shape[1], 1)
    cov = (resid @ resid / dof) * np.linalg.pinv(X.T @ X)
    return beta, np.sqrt(np.diag(cov))


# Pre-registered candidate features (harvey-liu-zhu-2016, bailey-lopezdeprado-2014).
# Written down BEFORE testing; nothing is added after seeing results. Each one
# is used only if it clears |t| >= 3 on the seasons it is fitted on.
MARGIN_FEATURES = {
    "rest": "pts of margin per day of rest advantage",
    "tz": "pts of margin per hour the away team traveled east",
    "form": "pts of margin per point of recent form gap (last 3 games vs expectation)",
    "qb": "pts of margin per point of starting-QB gap (QBTracker)",
}
TOTAL_FEATURES = {
    "wind_x": f"pts of total per mph above {config.WIND_THRESHOLD_MPH}",
    "cold": "pts of total when 32°F or colder",
}


def fit_environment(records, seasons):
    """Fit the candidate features on `seasons`, keep only those with |t| >= 3
    (strategy.T_STAT_BAR), then refit using only the kept ones."""
    from strategy import T_STAT_BAR
    fit = [r for r in records if r["season"] in seasons]

    def test(names, X_extra, y, intercept):
        X = [([1.0] if intercept else []) + [r["f"][n] for n in names] for r in fit]
        b, se = _ols(X, y)
        off = 1 if intercept else 0
        out = {n: {"coef": float(b[i + off]), "se": float(se[i + off]),
                   "t": float(b[i + off] / se[i + off]) if se[i + off] > 0 else 0.0} for i, n in enumerate(names)}
        for n in names:
            out[n]["accepted"] = abs(out[n]["t"]) >= T_STAT_BAR
        kept = [n for n in names if out[n]["accepted"]]
        applied = {n: 0.0 for n in names}
        if kept:
            Xk = [([1.0] if intercept else []) + [r["f"][n] for n in kept] for r in fit]
            bk, _ = _ols(Xk, y)
            for i, n in enumerate(kept):
                applied[n] = float(bk[i + off])
        return out, applied

    ym = [r["margin_actual"] - r["base_margin"] for r in fit]
    yt = [r["total_actual"] - r["base_total"] for r in fit]
    m_tests, m_applied = test(list(MARGIN_FEATURES), None, ym, intercept=False)
    t_tests, t_applied = test(list(TOTAL_FEATURES), None, yt, intercept=True)
    for n in m_tests:
        m_tests[n]["unit"] = MARGIN_FEATURES[n]
    for n in t_tests:
        t_tests[n]["unit"] = TOTAL_FEATURES[n]
    return {"games": len(fit), "seasons": [min(seasons), max(seasons)],
            "margin": m_tests, "total": t_tests, "applied": {**m_applied, **t_applied}}


def apply_environment(env, f):
    """Returns (margin adjustment, total adjustment) in points, using only accepted features."""
    a = env["applied"]
    dm = sum(a[n] * f[n] for n in MARGIN_FEATURES)
    dt = sum(a[n] * f[n] for n in TOTAL_FEATURES)
    return dm, dt


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def _logistic_fit(X, y, iters=50):
    """Logistic regression by Newton's method (IRLS). Returns (coefs, std errors, log-likelihood)."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = _sigmoid(X @ b)
        H = X.T @ (X * (p * (1 - p))[:, None]) + 1e-9 * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p))
        b += step
        if np.max(np.abs(step)) < 1e-10:
            break
    p = np.clip(_sigmoid(X @ b), 1e-12, 1 - 1e-12)
    ll = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))
    return b, np.sqrt(np.diag(np.linalg.inv(H))), ll


def raw_model_prob(r, env):
    dm, _ = apply_environment(env, r["f"])
    return margin_to_prob(r["base_margin"] + dm, config.NFL.ELO_PER_POINT)


def fit_combo(records, env, seasons):
    """Benter-style combination (benter-1994, bates-granger-1969):

        logit(p) = a0 + a1 * logit(market) + a2 * logit(model)

    fitted by maximum likelihood on `seasons`. a2 near 0 means the model adds
    nothing the market doesn't know. a1 above 1 means the market under-rates
    favorites (favorite-longshot bias, thaler-ziemba-1988).
    Also reports the market-only refit and the log-likelihood gain over the
    raw market, which is Benter's own yardstick.
    """
    fit = [r for r in records if r["season"] in seasons and r["market_p"] is not None]
    y = np.array([r["y"] for r in fit], float)
    lm = _logit([r["market_p"] for r in fit])
    lmod = _logit([raw_model_prob(r, env) for r in fit])
    ones = np.ones(len(fit))
    b, se, ll = _logistic_fit(np.column_stack([ones, lm, lmod]), y)
    bm, sem, llm = _logistic_fit(np.column_stack([ones, lm]), y)
    pm = np.clip(np.array([r["market_p"] for r in fit]), 1e-12, 1 - 1e-12)
    ll_raw = float(np.sum(y * np.log(pm) + (1 - y) * np.log(1 - pm)))
    # Same t >= 3 bar as every other feature: only use what clears it.
    from strategy import T_STAT_BAR
    t_model = float(b[2] / se[2]) if se[2] > 0 else 0.0
    t_recal = max(abs(bm[0] / sem[0]), abs((bm[1] - 1) / sem[1]))
    if abs(t_model) >= T_STAT_BAR:
        mode = "model+market"
    elif t_recal >= T_STAT_BAR:
        mode = "recalibrated market"
    else:
        mode = "market"
    return {"games": len(fit), "seasons": [min(seasons), max(seasons)], "mode": mode,
            "a0": float(b[0]), "a1": float(b[1]), "a2": float(b[2]),
            "se": [float(x) for x in se], "t_model": float(b[2] / se[2]) if se[2] > 0 else 0.0,
            "ll_gain_vs_market": ll - ll_raw,
            "market_only": {"a0": float(bm[0]), "a1": float(bm[1]), "se_a1": float(sem[1]),
                            "t_bias": float((bm[1] - 1) / sem[1]), "ll_gain_vs_market": llm - ll_raw}}


def combo_prob(c, market, model):
    """Apply a fitted combination in whatever mode cleared the t >= 3 bar.
    Falls back to the raw model when there is no market."""
    if market is None:
        return model
    if c["mode"] == "market":
        return float(market)
    if c["mode"] == "recalibrated market":
        m = c["market_only"]
        return float(_sigmoid(m["a0"] + m["a1"] * _logit(market)))
    return float(_sigmoid(c["a0"] + c["a1"] * _logit(market) + c["a2"] * _logit(model)))


def fit_sigma(records, seasons):
    """Spread of final margins around the closing line (stern-1991), fitted on our data."""
    res = [r["margin_actual"] + r["close_spread"] for r in records
           if r["season"] in seasons and r["close_spread"] is not None]
    return float(np.std(res, ddof=1))


def prob_to_margin(p):
    """Inverse of margin_to_prob: home win probability -> implied margin in points."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 400 / config.NFL.ELO_PER_POINT * np.log10(p / (1 - p))


def fit_epa_weight(records, seasons):
    """How much of the margin should come from EPA instead of Elo, fitted on `seasons`.

        margin_actual - elo = w * ramp * (epa - elo)      (least squares, no intercept)

    ramp grows from 0 to 1 over the first EPA_FULL_WEIGHT_GAMES games, as in
    RatingSystem.predict. The pre-set config.NFL.EPA_WEIGHT_MAX is the prior;
    the fitted w replaces it only if it differs from the prior by |t| >= 3
    (the same bar as every other fitted number)."""
    from strategy import T_STAT_BAR
    fit = [r for r in records if r["season"] in seasons and r["epa_margin"] is not None and r["epa_ramp"] > 0]
    x = np.array([r["epa_ramp"] * (r["epa_margin"] - r["elo_margin"]) for r in fit])
    y = np.array([r["margin_actual"] - r["elo_margin"] for r in fit])
    b, se = _ols(x[:, None], y)
    prior = config.NFL.EPA_WEIGHT_MAX
    t = float((b[0] - prior) / se[0]) if se[0] > 0 else 0.0
    accepted = abs(t) >= T_STAT_BAR
    w = float(np.clip(b[0], 0.0, 1.0)) if accepted else prior
    return {"games": len(fit), "fitted": float(b[0]), "se": float(se[0]), "t": t,
            "accepted": accepted, "w": w, "prior": config.NFL.EPA_WEIGHT_MAX}


def set_base_margins(records, w):
    """Recompute each record's pre-game margin with EPA weight w (no other change)."""
    for r in records:
        r["base_margin"] = r["elo_margin"] if r["epa_margin"] is None else \
            r["elo_margin"] + w * r["epa_ramp"] * (r["epa_margin"] - r["elo_margin"])


def fit_all(records, seasons):
    """Everything that is learned from data, fitted on `seasons` only.
    The EPA weight goes first because the other fits work on top of the margin it sets."""
    epa_w = fit_epa_weight(records, seasons)
    set_base_margins(records, epa_w["w"])
    env = fit_environment(records, seasons)
    return {"epa_w": epa_w, "env": env, "combo": fit_combo(records, env, seasons),
            "sigma": fit_sigma(records, seasons)}


# ----------------------------------------------------------- replay -------

def _season_now(now):
    return now.year if now.month >= 3 else now.year - 1


def replay(games_df, current_season, on_game=None):
    """Walk every completed game from NFL_START_SEASON forward, week by week.
    on_game(game, rs) is called BEFORE that week's results are learned."""
    rs = RatingSystem(config.NFL)
    seasons = range(config.NFL_START_SEASON, current_season + 1)
    df = games_df[games_df["season"].isin(seasons)].sort_values(["season", "week", "gameday"])
    for season, sgames in groupby((game_record(r) for _, r in df.iterrows()), key=lambda g: g["season"]):
        epa, _ = season_epa(season, current_season)
        rs.start_season()
        for _week, wgames in groupby(sgames, key=lambda g: g["week"]):
            wgames = list(wgames)
            if on_game:
                for g in wgames:
                    on_game(g, rs)
            for g in wgames:
                if g["completed"]:
                    rs.update(g, epa.get(g["id"], []))
    return rs


class FormTracker:
    """Recent form (jegadeesh-titman-1993 momentum, adapted): each team's average
    margin above the model's expectation over its last 3 games this season."""
    def __init__(self):
        self.season, self.res = None, {}

    def gap(self, g):
        if g["season"] != self.season:
            self.season, self.res = g["season"], {}
        f = lambda t: float(np.mean(self.res[t][-3:])) if self.res.get(t) else 0.0
        return f(g["home"]) - f(g["away"])

    def record(self, g, base_margin):
        resid = (g["home_pts"] - g["away_pts"]) - base_margin
        self.res.setdefault(g["home"], []).append(resid)
        self.res.setdefault(g["away"], []).append(-resid)


# QB model constants, fixed before any test (538-style QB adjustment, adapted):
QB_DECAY = 0.97            # each new game a QB plays shrinks his older games' weight by 3%
QB_PRIOR_DROPBACKS = 150   # pseudo-dropbacks of the prior mixed into every QB's average
QB_REPLACEMENT = -0.10     # prior = league EPA/dropback minus this (an unknown QB is a backup)
TEAM_QB_DECAY = 0.85       # how fast a team's "QB baseline" follows who actually plays
DROPBACKS_PER_GAME = 38    # converts EPA/dropback into points per game


class QBTracker:
    """Starting-QB adjustment. Elo and EPA learn a team's strength with the QBs
    who have been playing. When the listed starter is better or worse than that
    recent baseline, the gap is news the ratings haven't absorbed yet:

        qb feature = 38 * [(V_home_starter - home_baseline) - (V_away_starter - away_baseline)]

    V = a QB's decayed, shrunk EPA per dropback. baseline = each QB's share of
    the team's recent (decayed) dropbacks times his CURRENT V, so the usual
    starter starting again gives a gap of exactly 0. Only games already
    played feed V and baselines. The feature's size is fitted like every other
    feature (|t| >= 3 or it is not used)."""

    def __init__(self, current_season):
        self.current, self.season, self.games = current_season, None, {}
        self.S, self.N = {}, {}                 # qb -> decayed EPA sum, decayed dropbacks
        self.share = {}                         # team -> {qb: decayed dropbacks}
        self.lsum, self.ln = 0.0, 0             # league totals for the prior

    def _prior(self):
        return (self.lsum / self.ln if self.ln else 0.0) + QB_REPLACEMENT

    def value(self, qb):
        k = QB_PRIOR_DROPBACKS
        return (self.S.get(qb, 0.0) + k * self._prior()) / (self.N.get(qb, 0.0) + k)

    def _season(self, g):
        if g["season"] != self.season:
            self.season = g["season"]
            self.games = load_qb_games(g["season"], self.current)

    def baseline(self, team):
        sh = self.share.get(team)
        if not sh:
            return None
        return sum(n * self.value(q) for q, n in sh.items()) / sum(sh.values())

    def team_gap(self, team, qb):
        base = self.baseline(team)
        if not isinstance(qb, str) or base is None:
            return 0.0
        return self.value(qb) - base

    def gap(self, g):
        """Feature value in points for game g (home view), from games before it."""
        self._season(g)
        return DROPBACKS_PER_GAME * (self.team_gap(g["home"], g.get("home_qb_id"))
                                     - self.team_gap(g["away"], g.get("away_qb_id")))

    def record(self, g):
        """Learn from a finished game: update each QB who dropped back, then each team's baseline."""
        self._season(g)
        rows = self.games.get(g["id"], [])
        for team in {r["team"] for r in rows}:
            sh = self.share.setdefault(team, {})
            for q in sh:
                sh[q] *= TEAM_QB_DECAY
            for r in rows:
                if r["team"] == team:
                    sh[r["qb_id"]] = sh.get(r["qb_id"], 0.0) + r["dropbacks"]
            for q in [q for q, n in sh.items() if n < 1.0]:
                del sh[q]   # long-gone QBs drop out
        for r in rows:
            q = r["qb_id"]
            self.S[q] = QB_DECAY * self.S.get(q, 0.0) + r["epa"]
            self.N[q] = QB_DECAY * self.N.get(q, 0.0) + r["dropbacks"]
            self.lsum += r["epa"]
            self.ln += r["dropbacks"]


def collect_records(games_df, current_season):
    """Base (Elo+EPA) prediction for every completed game, made before it was played."""
    records, form, qbt = [], FormTracker(), QBTracker(current_season)

    def on_game(g, rs):
        gap = form.gap(g)
        qb = qbt.gap(g)
        if not g["completed"]:
            return
        qbt.record(g)  # this game's QB play only feeds LATER games
        p = rs.predict(g["home"], g["away"], g["neutral"])
        form.record(g, p["margin"])  # this game's result only feeds LATER games
        if g["home_pts"] == g["away_pts"]:
            return  # ties are rare and have no winner to score
        mkt = None
        if g["home_ml"] is not None and g["away_ml"] is not None:
            mkt = remove_vig(g["home_ml"], g["away_ml"])[0]
        records.append({
            "season": g["season"], "id": g["id"], "f": {**features(g), "form": gap, "qb": qb},
            "base_margin": p["margin"], "base_total": p["total"],
            "elo_margin": p["elo_margin"], "epa_margin": p["epa_margin"],
            "epa_ramp": p["epa_weight"] / config.NFL.EPA_WEIGHT_MAX,
            "margin_actual": g["home_pts"] - g["away_pts"],
            "total_actual": g["home_pts"] + g["away_pts"],
            "y": 1 if g["home_pts"] > g["away_pts"] else 0,
            # nflverse: positive spread_line = home favored. backtest.py uses
            # betting convention (negative = home favored), so flip the sign.
            "close_spread": None if g["spread_line"] is None else -g["spread_line"],
            "total_line": g["total_line"], "market_p": mkt,
            "home_ml": g["home_ml"], "away_ml": g["away_ml"],
            "home_spread_odds": g["home_spread_odds"], "away_spread_odds": g["away_spread_odds"],
        })

    replay(games_df, current_season, on_game)
    return records


# ------------------------------------------------------ betting sim -------

def simulate_bets(test, prob_key, kind="moneyline", min_ev=config.MIN_EDGE):
    """Bet the test seasons at CLOSING prices, the hardest benchmark there is:
    any profit here would be real edge, not just catching a stale line.

    A side is bet when EV >= 3% and the raw model is within 10 pts of the
    market (the project's rules). Reports flat-stake ROI with a bootstrap 95%
    CI and t-stat, plus a quarter-Kelly bankroll curve.
    """
    from strategy import (KELLY_FRACTION, MAX_BET_FRACTION, bootstrap_ci, cover_prob,
                          kelly_fraction_decimal, max_drawdown, t_stat)
    flat, bankroll, equity, hits = [], 1.0, [1.0], 0
    for r in test:
        if r["market_p"] is None:
            continue
        p_home = r[prob_key]
        raw_home = r["raw_p"]
        options = []
        if kind == "moneyline":
            for side, p, raw, mkt, ml in (("home", p_home, raw_home, r["market_p"], r["home_ml"]),
                                          ("away", 1 - p_home, 1 - raw_home, 1 - r["market_p"], r["away_ml"])):
                won = (r["y"] == 1) if side == "home" else (r["y"] == 0)
                options.append((p, raw, mkt, american_to_decimal(ml), won, False))
        else:
            if r["close_spread"] is None:
                continue
            # Center on the market's own line and shift only by how much the bet
            # probability disagrees with the market (no moneyline-vs-spread artifacts).
            m_bet = -r["close_spread"] + prob_to_margin(p_home) - prob_to_margin(r["market_p"])
            pc = cover_prob(m_bet, r["close_spread"], r["sigma"])
            cover = r["margin_actual"] + r["close_spread"]
            for side, p, ml in (("home", pc, r["home_spread_odds"]), ("away", 1 - pc, r["away_spread_odds"])):
                dec = american_to_decimal(ml if ml is not None else -110)
                won = cover > 0 if side == "home" else cover < 0
                options.append((p, raw_home if side == "home" else 1 - raw_home,
                                r["market_p"] if side == "home" else 1 - r["market_p"], dec, won, cover == 0))
        for p, raw, mkt, dec, won, push in options:
            if p * dec - 1 < min_ev or abs(raw - mkt) > config.MAX_MODEL_MARKET_GAP:
                continue
            ret = 0.0 if push else (dec - 1 if won else -1.0)
            flat.append(ret)
            hits += 1 if (won and not push) else 0
            f = min(kelly_fraction_decimal(p, dec) * KELLY_FRACTION, MAX_BET_FRACTION)
            bankroll *= 1 + f * ret
            equity.append(bankroll)
    lo, hi = bootstrap_ci(flat)
    return {"bets": len(flat), "roi": float(np.mean(flat)) if flat else None,
            "ci95": [lo, hi], "t": t_stat(flat), "hit_rate": hits / len(flat) if flat else None,
            "kelly_final": bankroll, "kelly_max_drawdown": max_drawdown(equity)}


def run_backtest(now=None):
    """Walk-forward (mclean-pontiff-2016): each test season is scored with
    weights fitted only on seasons before it (expanding window from 2015)."""
    games_df, stamp = load_games()
    last_test = config.NFL_TEST_SEASONS[-1]
    records = collect_records(games_df, last_test)

    per_season, test = [], []
    for S in config.NFL_TEST_SEASONS:
        seasons = list(range(config.NFL_FIT_SEASONS[0], S))
        fitted = fit_all(records, seasons)
        per_season.append({"season": S, **fitted})
        for r in (r for r in records if r["season"] == S):
            dm, dt = apply_environment(fitted["env"], r["f"])
            r["raw_p"] = margin_to_prob(r["base_margin"] + dm, config.NFL.ELO_PER_POINT)
            r["p"] = combo_prob(fitted["combo"], r["market_p"], r["raw_p"])
            r["margin_pred"] = prob_to_margin(r["p"])
            r["total_pred"] = r["base_total"] + dt
            r["sigma"] = fitted["sigma"]
            test.append(r)

    s = summarize(test)
    s["verdict"] = verdict(s["vs_market"])
    s["burn_in"] = [config.NFL_START_SEASON, config.NFL_TEST_SEASONS[0] - 1]
    s["source"] = f"nflverse schedules + team stats @ {stamp}"
    s["walk_forward"] = per_season
    s["env"] = per_season[-1]["env"]          # latest fit, for display
    s["combo"] = per_season[-1]["combo"]

    # The raw model (no market) for contrast: what you'd get trusting it alone.
    y = [r["y"] for r in test]
    raw = [r["raw_p"] for r in test]
    s["raw_model"] = {"brier": brier(raw, y), "log_loss": log_loss(raw, y),
                      "vs_market": paired_brier_diff(raw, [r["market_p"] for r in test], y)
                      if all(r["market_p"] is not None for r in test) else None,
                      "ats": ats_record([{**r, "margin_pred": prob_to_margin(r["raw_p"])} for r in test])}

    # Betting simulation at closing prices. 4 variants are run and ALL are
    # reported (bailey-lopezdeprado-2014: no picking the best afterwards).
    s["betting"] = {
        "raw_moneyline": simulate_bets(test, "raw_p", "moneyline"),
        "combo_moneyline": simulate_bets(test, "p", "moneyline"),
        "raw_spread": simulate_bets(test, "raw_p", "spread"),
        "combo_spread": simulate_bets(test, "p", "spread"),
    }
    s["trials"] = len(s["betting"])

    def ou(key):
        w = l = p = 0
        for r in test:
            if r["total_line"] is None or r[key] == r["total_line"]:
                continue
            if r["total_actual"] == r["total_line"]:
                p += 1
            elif (r[key] > r["total_line"]) == (r["total_actual"] > r["total_line"]):
                w += 1
            else:
                l += 1
        return {"w": w, "l": l, "p": p, "pct": w / (w + l) if w + l else None}

    s["totals"] = {
        "mae_model": float(np.mean([abs(r["total_pred"] - r["total_actual"]) for r in test])),
        "mae_market": float(np.mean([abs(r["total_line"] - r["total_actual"])
                                     for r in test if r["total_line"] is not None])),
        "ou_with_env": ou("total_pred"), "ou_without_env": ou("base_total"),
    }
    return s


# --------------------------------------------------------- live slate -----

def _forecast(g):
    """Open-Meteo hourly forecast at kickoff for an outdoor home stadium."""
    if g["neutral"] or g["roof"] not in OUTDOOR or not g["gametime"]:
        return None, None
    lat, lon = T.TEAMS[g["home"]][3:5]
    params = {"latitude": lat, "longitude": lon, "timezone": "America/New_York",
              "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m",
              "wind_speed_unit": "mph", "temperature_unit": "fahrenheit",
              "start_date": g["gameday"], "end_date": g["gameday"]}
    data, stamp = cached_get("https://api.open-meteo.com/v1/forecast", params=params,
                             ttl=3600, source="Open-Meteo")
    hour = f"{g['gameday']}T{g['gametime'][:2]}:00"
    h = data.get("hourly", {})
    if hour not in h.get("time", []):
        return None, stamp
    i = h["time"].index(hour)
    pick = lambda k: (h.get(k) or [None] * (i + 1))[i]
    return {"wind": pick("wind_speed_10m"), "gusts": pick("wind_gusts_10m"),
            "temp": pick("temperature_2m"), "precip_prob": pick("precipitation_probability")}, stamp


SNAPSHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_price_snapshots.csv")
SNAPSHOT_FIELDS = ["logged_at", "odds_stamp", "kalshi_stamp", "game_id", "kickoff", "home", "away",
                   "market_prob", "prob_source", "sharp_prob", "consensus_prob", "home_spread", "total",
                   "kalshi_home_ask", "kalshi_away_ask"]


def log_snapshot(rows, odds_stamp, kalshi_stamp, path=None):
    """Append one row per game with the live prices just fetched. Over time this
    becomes the line-movement history nflverse doesn't have (it keeps only the
    close), which a Kalshi backtest and line-movement features need. A cached
    response that was already logged is not logged again."""
    path = path or SNAPSHOTS
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = set()
    if os.path.exists(path):
        with open(path, newline="") as f:
            seen = {(r["game_id"], r["odds_stamp"], r["kalshi_stamp"]) for r in csv.DictReader(f)}
    new = []
    for r in rows:
        key = (r["game"]["id"], odds_stamp or "", kalshi_stamp or "")
        if key in seen:
            continue
        seen.add(key)
        m, k = r["market"] or {}, r["kalshi"] or {}
        rnd = lambda x: "" if x is None else round(x, 4)
        new.append({"logged_at": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%SZ"),
                    "odds_stamp": odds_stamp or "", "kalshi_stamp": kalshi_stamp or "",
                    "game_id": r["game"]["id"], "kickoff": r["game"]["start"] or "",
                    "home": r["game"]["home_code"], "away": r["game"]["away_code"],
                    "market_prob": rnd(m.get("home_prob")), "prob_source": m.get("prob_source", "nflverse"),
                    "sharp_prob": rnd(m.get("sharp_prob")), "consensus_prob": rnd(m.get("consensus_prob")),
                    "home_spread": rnd(m.get("home_spread")), "total": rnd(m.get("total")),
                    "kalshi_home_ask": rnd(k.get("home", {}).get("yes_ask")),
                    "kalshi_away_ask": rnd(k.get("away", {}).get("yes_ask"))})
    if new:
        write_header = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new)
    return len(new)


def _kickoff(g):
    """Kickoff as an aware ET datetime, or None if the schedule has no time."""
    if not g.get("gametime"):
        return None
    return datetime.strptime(f"{g['gameday']} {g['gametime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)


def _odds_events():
    # Naming up to 10 books costs the same credits as one region, and lets us
    # add Pinnacle (an EU book) as the sharp reference next to the US books.
    params = {"apiKey": config.get_key("ODDS_API_KEY"), "bookmakers": ",".join(config.NFL_ODDS_BOOKS),
              "markets": "h2h,spreads,totals", "oddsFormat": "american", "dateFormat": "iso"}
    return cached_get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
                      params=params, ttl=config.TTL_ODDS, source="The Odds API")


def _kalshi_nfl():
    from api_clients import kalshi_markets
    return kalshi_markets(KALSHI_NFL_SERIES)


def _match_kalshi(markets, home, away):
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], []).append(m)
    found = []
    for ms in by_event.values():
        coded = {T.code_for(m["team"]): m for m in ms}
        if home in coded and away in coded:
            found.append({"home": coded[home], "away": coded[away]})
    return found[0] if len(found) == 1 else None


def build_slate(now=None, days=0):
    """Today's NFL games (or the next `days` days) with model, market,
    Kalshi and environment numbers. Returns (info, rows)."""
    now = now or datetime.now(ET)
    season = _season_now(now)
    games_df, games_stamp = load_games()

    # Everything learned from data is refit on every completed season before
    # this one (walk-forward, same procedure the backtest validated).
    fit_seasons = list(range(config.NFL_FIT_SEASONS[0], season))
    records = collect_records(games_df, season - 1)
    fitted = fit_all(records, fit_seasons)
    env, combo, sigma = fitted["env"], fitted["combo"], fitted["sigma"]

    form, form_gap = FormTracker(), {}
    qbt, qb_gap = QBTracker(season), {}

    def track_form(g, rs_):
        gap, qb = form.gap(g), qbt.gap(g)
        if g["completed"]:
            form.record(g, rs_.predict(g["home"], g["away"], g["neutral"])["margin"])
            qbt.record(g)
        elif g["season"] == season:
            form_gap[g["id"]] = gap
            qb_gap[g["id"]] = qb

    rs = replay(games_df, season, track_form)
    first, last = now.date(), now.date() + timedelta(days=days)
    df = games_df[(games_df["season"] == season) & games_df["home_score"].isna()]
    slate = [game_record(r) for _, r in df.iterrows()
             if first <= datetime.strptime(r["gameday"], "%Y-%m-%d").date() <= last]
    slate.sort(key=lambda g: (g["gameday"], g["gametime"] or ""))

    notes = []
    # A game with no final score yet may already be under way (nflverse fills
    # scores in later). In-game prices are not pregame edges, so drop it.
    started = [g for g in slate if _kickoff(g) is not None and _kickoff(g) <= now]
    if started:
        slate = [g for g in slate if g not in started]
        notes.append("Skipped (already kicked off): " +
                     "; ".join(f"{T.full_name(g['away'])} @ {T.full_name(g['home'])}" for g in started))
    try:
        events, odds_stamp = _odds_events()
    except (config.MissingKeyError, ApiError) as e:
        events, odds_stamp = None, None
        notes.append(f"Live sportsbook odds unavailable ({e}). Using the nflverse schedule line instead.")
    try:
        markets, kalshi_stamp = _kalshi_nfl()
    except ApiError as e:
        markets, kalshi_stamp = None, None
        notes.append(f"Kalshi unavailable ({e}).")
    injury_pts, inj_expired, inj_undated = inj.injury_report(now=now)
    if inj_expired:
        notes.append(f"Ignored {len(inj_expired)} injuries.csv row(s) older than {inj.MAX_AGE_HOURS} h "
                     "(the market has priced them by now): " + ", ".join(p for _, p in inj_expired))
    if inj_undated:
        notes.append(f"{len(inj_undated)} injuries.csv row(s) have no 'entered' date, so they never expire. "
                     "Add one to avoid counting old news twice.")
    weather_failed = False

    rows = []
    for g in slate:
        wx, wx_stamp = None, None
        if not weather_failed:
            try:
                wx, wx_stamp = _forecast(g)
            except ApiError as e:
                weather_failed = True
                notes.append(f"Weather forecast unavailable ({e}). No wind/cold adjustment applied.")
        f = {**features(g, wind=wx["wind"] if wx else None, temp=wx["temp"] if wx else None),
             "form": form_gap.get(g["id"], 0.0), "qb": qb_gap.get(g["id"], 0.0)}
        if wx is None:  # no forecast: no wind/cold adjustment
            f["wind_x"], f["cold"] = 0.0, 0.0
        dm, dt = apply_environment(env, f)
        home_name, away_name = T.full_name(g["home"]), T.full_name(g["away"])
        injury_adj = sum(injury_pts.get(a, 0.0) for a in T.aliases(g["away"])) - \
            sum(injury_pts.get(a, 0.0) for a in T.aliases(g["home"]))
        p = rs.predict(g["home"], g["away"], g["neutral"], injury_adj=dm, epa_weight_max=fitted["epa_w"]["w"])
        p["total"] += dt
        p["env_margin"], p["env_total"], p["injury_adj"] = dm, dt, injury_adj
        p["raw_home_prob"] = p["home_prob"]

        # Market: live Odds API if possible, else the nflverse line.
        mkt, mkt_source = None, None
        if events is not None:
            for e in events:
                h, a = T.code_for(e["home_team"]), T.code_for(e["away_team"])
                if (h, a) in ((g["home"], g["away"]), (g["away"], g["home"])):
                    oh, oa = (e["home_team"], e["away_team"]) if h == g["home"] else (e["away_team"], e["home_team"])
                    mkt = parse_odds_event(e, oh, oa)
                    mkt_source = (f"The Odds API, {mkt['prob_source']} no-vig; {mkt['ml_books']} bettable "
                                  f"book{'s' if mkt['ml_books'] != 1 else ''}, book update {mkt['updated']}")
                    break
        if mkt is None and g["home_ml"] is not None and g["away_ml"] is not None:
            mkt = {"home_prob": remove_vig(g["home_ml"], g["away_ml"])[0], "ml_books": 1,
                   "home_spread": None if g["spread_line"] is None else -g["spread_line"],
                   "total": g["total_line"], "home_ml_dec": american_to_decimal(g["home_ml"]),
                   "away_ml_dec": american_to_decimal(g["away_ml"]), "updated": games_stamp,
                   "home_spread_dec": american_to_decimal(g["home_spread_odds"]) if g["home_spread_odds"] else None,
                   "away_spread_dec": american_to_decimal(g["away_spread_odds"]) if g["away_spread_odds"] else None}
            mkt_source = f"nflverse schedule file (one consensus line, as of its last update) @ {games_stamp}"

        # Bet probability: the Benter-style combination, in whatever mode
        # cleared the t >= 3 bar (often just the market itself).
        p["home_prob"] = combo_prob(combo, mkt["home_prob"] if mkt else None, p["raw_home_prob"])
        p["bet_basis"] = combo["mode"] if mkt else "raw model (no market)"
        if injury_adj:
            # Your injuries.csv is news the market may not have priced yet, so it
            # moves the blended number directly (as a shift in points of margin).
            p["home_prob"] = margin_to_prob(prob_to_margin(p["home_prob"]) + injury_adj, config.NFL.ELO_PER_POINT)
            p["margin"] += injury_adj
            p["home_spread"] -= injury_adj

        km = _match_kalshi(markets, g["home"], g["away"]) if markets else None
        best = None
        for side, team, prob in (("home", home_name, p["home_prob"]), ("away", away_name, 1 - p["home_prob"])):
            if not km or km[side]["yes_ask"] is None or not 0 < km[side]["yes_ask"] < 1:
                continue
            m_side = None if not mkt else (mkt["home_prob"] if side == "home" else 1 - mkt["home_prob"])
            e = edge_after_fee(prob, km[side]["yes_ask"])
            if best is None or e > best[0]:
                best = (e, team, prob, m_side, km[side]["yes_ask"])
        # 10-pt rule uses the RAW model vs the market: a big gap means the model is missing news.
        raw_side = None
        if best:
            raw_side = p["raw_home_prob"] if best[1] == home_name else 1 - p["raw_home_prob"]
        flag = bool(best) and should_flag(best[0], raw_side, best[3], price=best[4])

        kick = _kickoff(g)
        kick = kick.isoformat() if kick else None
        rows.append({
            "game": {"id": g["id"], "home": home_name, "away": away_name, "home_code": g["home"],
                     "away_code": g["away"], "start": kick, "neutral": g["neutral"],
                     "stadium": g["stadium"], "roof": g["roof"]},
            "pred": p, "market": mkt, "kalshi": km, "best": best, "flag": flag, "sigma": sigma,
            "env": {**f, "forecast": wx, "home_rest": g["home_rest"], "away_rest": g["away_rest"],
                    "home_qb": g["home_qb"], "away_qb": g["away_qb"],
                    "qb_pts": env["applied"].get("qb", 0.0) * f["qb"]},
            "sources": {
                "model": f"nflverse schedules + team EPA @ {games_stamp}",
                "market": mkt_source or "no odds for this game",
                "kalshi": (f"Kalshi {km['home']['event_ticker']} @ {kalshi_stamp}" if km
                           else ("Kalshi: unavailable" if markets is None else f"Kalshi @ {kalshi_stamp}: no market")),
                "weather": (f"Open-Meteo forecast @ {wx_stamp}" if wx else
                            "Indoor / retractable roof or neutral site: no weather adjustment"
                            if g["roof"] not in OUTDOOR or g["neutral"] else "Forecast unavailable"),
            },
        })
    if events is not None or markets is not None:
        log_snapshot(rows, odds_stamp, kalshi_stamp)
    info = {"sport": "nfl", "label": f"NFL · {first.strftime('%a %b %-d')}" + (f"–{last.strftime('%b %-d')}" if days else ""),
            "env": env, "combo": combo, "sigma": sigma, "bet_basis": combo["mode"], "notes": notes}
    return info, rows


def report(s):
    """Plain-text NFL backtest report."""
    from tabulate import tabulate
    from strategy import T_STAT_BAR
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    ats = lambda a: f"{a['w']}-{a['l']}-{a['p']} ({a['pct']:.1%})" if a["pct"] is not None else "n/a"
    out = [f"NFL BACKTEST (walk-forward). Ratings start {s['burn_in'][0]}; each test season is fitted only on "
           f"{config.NFL_FIT_SEASONS[0]}..(season-1). Source: {s['source']}",
           f"Rules fixed in advance: features need |t| >= {T_STAT_BAR:.0f}; bets need EV >= 3% and model within 10 pts "
           f"of market; {s['trials']} betting variants run and all reported.\n"]
    out.append(tabulate([[r["label"], r["games"], f"{r['accuracy']:.1%}", f"{r['brier']:.4f}",
                          f"{r['log_loss']:.4f}", ats(r["ats"])] for r in s["seasons"]],
                        headers=["Season", "Games", "SU accuracy", "Brier", "Log loss", "ATS vs close"]))
    vm, rm = s["vs_market"], s["raw_model"]
    out.append("\n" + tabulate([
        ["Bet probability (final model)", f"{vm['model']['brier']:.4f}", f"{vm['model']['log_loss']:.4f}"],
        ["Raw Elo+EPA model alone", f"{rm['brier']:.4f}", f"{rm['log_loss']:.4f}"],
        ["Closing market (no-vig)", f"{vm['market']['brier']:.4f}", f"{vm['market']['log_loss']:.4f}"]],
        headers=[f"Test seasons ({vm['games']} games)", "Brier", "Log loss"]))
    if rm.get("vs_market"):
        d = rm["vs_market"]
        out.append(f"Raw model minus market, Brier (paired bootstrap): {d['diff']:+.4f}, "
                   f"95% CI [{d['ci95'][0]:+.4f}, {d['ci95'][1]:+.4f}], t = {d['t']:+.2f}  (negative = model better)")
    out.append(f"\n>>> {s['verdict']}\n")

    out.append(f"EPA WEIGHT (EPA source: {config.NFL_EPA_SOURCE}), refit each season: "
               "margin - elo = w * ramp * (epa - elo)")
    out.append(tabulate([[w["season"], w["epa_w"]["games"], f"{w['epa_w']['fitted']:.3f}", f"{w['epa_w']['se']:.3f}",
                          f"{w['epa_w']['t']:+.2f}", f"{w['epa_w']['w']:.3f}",
                          "fitted" if w["epa_w"]["accepted"] else f"prior {w['epa_w']['prior']}"]
                         for w in s["walk_forward"]],
                        headers=["Season", "Fit games", "Fitted w", "Std err", "t vs prior", "Used w", "Source"]))
    out.append("")

    out.append("BENTER-STYLE COMBINATION, refit each season: logit(p) = a0 + a1*logit(market) + a2*logit(model)")
    out.append(tabulate([[w["season"], w["combo"]["games"], f"{w['combo']['a1']:.3f}", f"{w['combo']['a2']:+.3f}",
                          f"{w['combo']['t_model']:+.2f}", f"{w['combo']['ll_gain_vs_market']:+.2f}",
                          f"{w['combo']['market_only']['a1']:.3f} (t vs 1: {w['combo']['market_only']['t_bias']:+.2f})",
                          w["combo"]["mode"], f"{w['sigma']:.2f}"] for w in s["walk_forward"]],
                        headers=["Season", "Fit games", "a1 market", "a2 model", "t(model)", "LL gain",
                                 "Market-only slope", "Used", "Spread sigma"]))
    out.append("  a2 near 0 = model adds nothing beyond the market. Market-only slope above 1 = favorite-longshot bias.")
    out.append("  'Used' is what cleared |t| >= 3; otherwise the plain market is used.\n")

    e = s["env"]
    out.append(f"CANDIDATE FEATURES (latest fit, {e['games']} games, {e['seasons'][0]}-{e['seasons'][1]}):")
    rows = [[n, f"{v['coef']:+.3f}", f"{v['se']:.3f}", f"{v['t']:+.2f}", "USED" if v["accepted"] else "rejected", v["unit"]]
            for grp in ("margin", "total") for n, v in e[grp].items()]
    out.append(tabulate(rows, headers=["Feature", "Effect", "Std err", "t", "Decision", "Meaning"]))

    out.append("\nBETTING SIMULATION at closing prices (test seasons, flat 1 unit; Kelly = quarter Kelly, 2% cap):")
    b = s["betting"]
    out.append(tabulate([[k.replace("_", " "), v["bets"], pct(v["roi"]),
                          "n/a" if v["ci95"][0] is None else f"[{v['ci95'][0]:+.1%}, {v['ci95'][1]:+.1%}]",
                          "n/a" if v["t"] is None else f"{v['t']:+.2f}", pct(v["hit_rate"]),
                          f"{v['kelly_final']:.3f}x", f"{v['kelly_max_drawdown']:.1%}"] for k, v in b.items()],
                        headers=["Variant", "Bets", "ROI", "95% CI", "t", "Hit rate", "Kelly bankroll", "Max drawdown"]))
    out.append(f"  An edge counts as real only if t >= {T_STAT_BAR:.0f} (harvey-liu-zhu-2016) and the CI excludes 0.")
    t = s["totals"]
    out.append(f"\nTOTALS: model MAE {t['mae_model']:.2f} vs closing total MAE {t['mae_market']:.2f}. "
               f"O/U vs close: {ats(t['ou_with_env'])} with weather, {ats(t['ou_without_env'])} without.\n")
    out.append("CALIBRATION of the bet probability:")
    out.append(tabulate([[f"{x['lo']:.0%}-{x['hi']:.0%}", x["games"], f"{x['avg_pred']:.1%}", f"{x['actual']:.1%}"]
                         for x in s["calibration"]], headers=["Predicted", "Games", "Avg predicted", "Actual"]))
    out.append("\nSources for every method: data/research.json")
    return "\n".join(out)
