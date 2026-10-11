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
from datetime import datetime, timedelta
from itertools import groupby
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
import injuries as inj
import nfl_teams as T
from backtest import ats_record, brier, calibration_bins, log_loss, summarize, verdict
from cache import ApiError, cached_download, cached_get
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
        "stadium": None if pd.isna(r.get("stadium")) else str(r["stadium"]),
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


def fit_environment(records):
    """Fit the rest/travel effect on margin and the wind/cold effect on total,
    using ONLY the fit seasons (2015-2022)."""
    fit = [r for r in records if r["season"] in config.NFL_FIT_SEASONS]
    Xm = [[r["f"]["rest"], r["f"]["tz"]] for r in fit]
    ym = [r["margin_actual"] - r["base_margin"] for r in fit]
    bm, sm = _ols(Xm, ym)
    Xt = [[1.0, r["f"]["wind_x"], r["f"]["cold"]] for r in fit]
    yt = [r["total_actual"] - r["base_total"] for r in fit]
    bt, st = _ols(Xt, yt)
    return {
        "games": len(fit), "seasons": [config.NFL_FIT_SEASONS[0], config.NFL_FIT_SEASONS[-1]],
        "rest": {"coef": bm[0], "se": sm[0], "unit": "pts of margin per day of rest advantage"},
        "tz": {"coef": bm[1], "se": sm[1], "unit": "pts of margin per hour the away team traveled east"},
        "wind": {"coef": bt[1], "se": st[1], "unit": f"pts of total per mph above {config.WIND_THRESHOLD_MPH}"},
        "cold": {"coef": bt[2], "se": st[2], "unit": "pts of total when 32°F or colder"},
    }


def model_prob(r, env):
    dm, _ = apply_environment(env, r["f"])
    return margin_to_prob(r["base_margin"] + dm, config.NFL.ELO_PER_POINT)


def fit_blend(records, env):
    """How much should the model be trusted versus the market?

    blended = (1 - w) * market + w * model. Pick the w (0 to 1, step 0.05)
    with the lowest log loss on the FIT seasons only. w = 0 means "the model
    adds nothing the market doesn't already know".
    """
    fit = [r for r in records if r["season"] in config.NFL_FIT_SEASONS and r["market_p"] is not None]
    best = None
    for w in np.arange(0, 1.0001, 0.05):
        probs = [(1 - w) * r["market_p"] + w * model_prob(r, env) for r in fit]
        ll = log_loss(probs, [r["y"] for r in fit])
        if best is None or ll < best[1]:
            best = (float(round(w, 2)), ll)
    return best[0]


def prob_to_margin(p):
    """Inverse of margin_to_prob: home win probability -> implied margin in points."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 400 / config.NFL.ELO_PER_POINT * np.log10(p / (1 - p))


def blend(model, market, w):
    """Blend one probability with the market (falls back to the model if no market)."""
    return model if market is None else (1 - w) * market + w * model


def apply_environment(env, f):
    """Returns (margin adjustment, total adjustment) in points."""
    dm = env["rest"]["coef"] * f["rest"] + env["tz"]["coef"] * f["tz"]
    dt = env["wind"]["coef"] * f["wind_x"] + env["cold"]["coef"] * f["cold"]
    return dm, dt


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
        epa, _ = load_team_epa(season, current_season)
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


def collect_records(games_df, current_season):
    """Base (Elo+EPA) prediction for every completed game, made before it was played."""
    records = []

    def on_game(g, rs):
        if not g["completed"] or g["home_pts"] == g["away_pts"]:
            return  # ties are rare and have no winner to score
        p = rs.predict(g["home"], g["away"], g["neutral"])
        mkt = None
        if g["home_ml"] is not None and g["away_ml"] is not None:
            mkt = remove_vig(g["home_ml"], g["away_ml"])[0]
        records.append({
            "season": g["season"], "id": g["id"], "f": features(g),
            "base_margin": p["margin"], "base_total": p["total"],
            "margin_actual": g["home_pts"] - g["away_pts"],
            "total_actual": g["home_pts"] + g["away_pts"],
            "y": 1 if g["home_pts"] > g["away_pts"] else 0,
            # nflverse: positive spread_line = home favored. backtest.py uses
            # betting convention (negative = home favored), so flip the sign.
            "close_spread": None if g["spread_line"] is None else -g["spread_line"],
            "total_line": g["total_line"], "market_p": mkt,
        })

    replay(games_df, current_season, on_game)
    return records


def run_backtest(now=None):
    now = now or datetime.now(ET)
    games_df, stamp = load_games()
    last_test = config.NFL_TEST_SEASONS[-1]
    records = collect_records(games_df, last_test)
    env = fit_environment(records)
    w = fit_blend(records, env)

    test = [r for r in records if r["season"] in config.NFL_TEST_SEASONS]
    for r in test:
        dm, dt = apply_environment(env, r["f"])
        r["margin_pred"] = r["base_margin"] + dm
        r["p"] = margin_to_prob(r["margin_pred"], config.NFL.ELO_PER_POINT)
        r["total_pred"] = r["base_total"] + dt

    s = summarize(test)
    s["verdict"] = verdict(s["vs_market"])
    s["burn_in"] = [config.NFL_START_SEASON, config.NFL_TEST_SEASONS[0] - 1]
    s["env"] = env
    s["source"] = f"nflverse schedules + team stats @ {stamp}"

    # Did the environment adjustments help on the test seasons? (ablation)
    y = [r["y"] for r in test]
    base_p = [margin_to_prob(r["base_margin"], config.NFL.ELO_PER_POINT) for r in test]
    base_ats = ats_record([{**r, "margin_pred": r["base_margin"]} for r in test])
    full_ats = ats_record(test)
    s["ablation"] = {
        "without_env": {"brier": brier(base_p, y), "log_loss": log_loss(base_p, y), "ats": base_ats},
        "with_env": {"brier": brier([r["p"] for r in test], y),
                     "log_loss": log_loss([r["p"] for r in test], y), "ats": full_ats},
    }

    # Totals: over/under record vs the closing total (weather mostly moves totals).
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

    both = [r for r in test if r["market_p"] is not None]
    yb = [r["y"] for r in both]
    bl = [blend(r["p"], r["market_p"], w) for r in both]
    s["blend"] = {"weight": w, "games": len(both), "brier": brier(bl, yb), "log_loss": log_loss(bl, yb),
                  "market_brier": brier([r["market_p"] for r in both], yb),
                  "market_log_loss": log_loss([r["market_p"] for r in both], yb)}

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


def _odds_events():
    params = {"apiKey": config.get_key("ODDS_API_KEY"), "regions": "us",
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

    # Environment effects: fitted on 2015-2022 only (same as the backtest).
    records = collect_records(games_df, config.NFL_FIT_SEASONS[-1])
    env = fit_environment(records)
    w = fit_blend(records, env)

    rs = replay(games_df, season)
    first, last = now.date(), now.date() + timedelta(days=days)
    df = games_df[(games_df["season"] == season) & games_df["home_score"].isna()]
    slate = [game_record(r) for _, r in df.iterrows()
             if first <= datetime.strptime(r["gameday"], "%Y-%m-%d").date() <= last]
    slate.sort(key=lambda g: (g["gameday"], g["gametime"] or ""))

    notes = []
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
    injury_pts = inj.load_injuries()
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
        f = features(g, wind=wx["wind"] if wx else None, temp=wx["temp"] if wx else None)
        if wx is None:  # no forecast: only rest and travel adjust (wind/cold left at 0)
            f["wind_x"], f["cold"] = 0.0, 0.0
        dm, dt = apply_environment(env, f)
        home_name, away_name = T.full_name(g["home"]), T.full_name(g["away"])
        injury_adj = sum(injury_pts.get(a, 0.0) for a in T.aliases(g["away"])) - \
            sum(injury_pts.get(a, 0.0) for a in T.aliases(g["home"]))
        p = rs.predict(g["home"], g["away"], g["neutral"], injury_adj=dm)
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
                    mkt_source = f"The Odds API, {mkt['ml_books']} book{'s' if mkt['ml_books'] != 1 else ''}, book update {mkt['updated']}"
                    break
        if mkt is None and g["home_ml"] is not None and g["away_ml"] is not None:
            mkt = {"home_prob": remove_vig(g["home_ml"], g["away_ml"])[0], "ml_books": 1,
                   "home_spread": None if g["spread_line"] is None else -g["spread_line"],
                   "total": g["total_line"], "home_ml_dec": american_to_decimal(g["home_ml"]),
                   "away_ml_dec": american_to_decimal(g["away_ml"]), "updated": games_stamp}
            mkt_source = f"nflverse schedule file (one consensus line, as of its last update) @ {games_stamp}"

        # Bets use the market-blended probability (weight fitted on 2015-2022).
        p["home_prob"] = blend(p["raw_home_prob"], mkt["home_prob"] if mkt else None, w)
        p["blend_weight"] = w if mkt else 1.0
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
        flag = bool(best) and should_flag(best[0], raw_side, best[3])

        kick = None
        if g["gametime"]:
            kick = datetime.strptime(f"{g['gameday']} {g['gametime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET).isoformat()
        rows.append({
            "game": {"id": g["id"], "home": home_name, "away": away_name, "home_code": g["home"],
                     "away_code": g["away"], "start": kick, "neutral": g["neutral"],
                     "stadium": g["stadium"], "roof": g["roof"]},
            "pred": p, "market": mkt, "kalshi": km, "best": best, "flag": flag,
            "env": {**f, "forecast": wx, "home_rest": g["home_rest"], "away_rest": g["away_rest"]},
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
    info = {"sport": "nfl", "label": f"NFL · {first.strftime('%a %b %-d')}" + (f"–{last.strftime('%b %-d')}" if days else ""),
            "env": env, "blend_weight": w, "notes": notes}
    return info, rows


def report(s):
    """Plain-text NFL backtest report (reuses the college report layout)."""
    from tabulate import tabulate
    out = [f"NFL BACKTEST: walk-forward, ratings start {s['burn_in'][0]}, scored seasons "
           f"{config.NFL_TEST_SEASONS[0]}-{config.NFL_TEST_SEASONS[-1]}. Source: {s['source']}",
           "Parameters fixed in config.py; rest/travel/weather effects fitted on "
           f"{s['env']['seasons'][0]}-{s['env']['seasons'][1]} only.\n"]
    ats = lambda a: f"{a['w']}-{a['l']}-{a['p']} ({a['pct']:.1%})" if a["pct"] is not None else "n/a"
    out.append(tabulate([[r["label"], r["games"], f"{r['accuracy']:.1%}", f"{r['brier']:.4f}",
                          f"{r['log_loss']:.4f}", ats(r["ats"])] for r in s["seasons"]],
                        headers=["Season", "Games", "SU accuracy", "Brier", "Log loss", "ATS vs close"]))
    vm = s["vs_market"]
    if vm:
        out.append("\n" + tabulate([[n, f"{x['accuracy']:.1%}", f"{x['brier']:.4f}", f"{x['log_loss']:.4f}"]
                                     for n, x in (("Model", vm["model"]), ("Closing market (no-vig)", vm["market"]))],
                                    headers=[f"vs CLOSING LINE ({vm['games']} games)", "SU accuracy", "Brier", "Log loss"]))
    out.append(f"\n>>> {s['verdict']}\n")
    out.append(f"ENVIRONMENT EFFECTS (fitted on {s['env']['games']} games, {s['env']['seasons'][0]}-{s['env']['seasons'][1]}):")
    out.append(tabulate([[k, f"{s['env'][k]['coef']:+.3f}", f"{s['env'][k]['se']:.3f}", s["env"][k]["unit"]]
                         for k in ("rest", "tz", "wind", "cold")], headers=["Factor", "Effect", "Std err", "Meaning"]))
    out.append("  An effect smaller than about 2x its std err could easily be noise.\n")
    a = s["ablation"]
    out.append(tabulate([[n, f"{x['brier']:.4f}", f"{x['log_loss']:.4f}", "{}-{}-{}".format(*x['ats'])]
                         for n, x in (("Without environment", a["without_env"]), ("With environment", a["with_env"]))],
                        headers=["Test seasons", "Brier", "Log loss", "ATS"]))
    b = s["blend"]
    out.append(f"\nTRUST WEIGHT: blended = {1 - b['weight']:.2f} x market + {b['weight']:.2f} x model "
               f"(weight fitted on {config.NFL_FIT_SEASONS[0]}-{config.NFL_FIT_SEASONS[-1]}).")
    out.append(f"  Test seasons: blend Brier {b['brier']:.4f} vs market {b['market_brier']:.4f}; "
               f"log loss {b['log_loss']:.4f} vs {b['market_log_loss']:.4f}.")
    out.append("  Live bets use this blended probability, so the model only moves prices as far as it has earned.")
    t = s["totals"]
    out.append(f"\nTOTALS: model MAE {t['mae_model']:.2f} vs closing total MAE {t['mae_market']:.2f} points. "
               f"O/U record vs close: {ats(t['ou_with_env'])} with weather, {ats(t['ou_without_env'])} without.\n")
    out.append("CALIBRATION (home-team win probability):")
    out.append(tabulate([[f"{b['lo']:.0%}-{b['hi']:.0%}", b["games"], f"{b['avg_pred']:.1%}", f"{b['actual']:.1%}"]
                         for b in s["calibration"]], headers=["Predicted", "Games", "Avg predicted", "Actual"]))
    return "\n".join(out)
