"""Live NFL win probability: game state from ESPN, a model trained on nflverse
play-by-play, and Kalshi's live winner prices side by side.

Read-only. ESPN's feed runs ~10-30 s behind TV and Kalshi traders watch TV, so a
gap here is often just a stale ESPN state, not an edge. The recorder
(data/live_snapshots.csv) exists to measure whether the gaps ever pay off.
"""
import csv
import os
import re
import time
from datetime import datetime, timezone

import nfl_teams as T
from cache import ApiError, cached_get

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SNAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_snapshots.csv")
SNAP_FIELDS = ["at", "event_id", "home", "away", "home_score", "away_score", "period", "clock", "sec_left",
               "home_ball", "down", "distance", "yardline_100", "home_to", "away_to", "home_line",
               "spread", "espn_wp", "model_wp", "kalshi_home_bid", "kalshi_home_ask", "kalshi_away_bid", "kalshi_away_ask"]


def _yardline_100(possession_text, offense):
    """Yards to the opponent's end zone for the offense. ESPN writes 'ATL 2' (on
    ATL's side of the field) or '50'."""
    m = re.match(r"([A-Z]+)\s+(\d+)", possession_text or "")
    if not m:
        return 50 if (possession_text or "").strip() == "50" else None
    side, n = m.group(1), int(m.group(2))
    return 100 - n if side == offense else n


def parse_state(event):
    """One ESPN scoreboard event -> live state dict (home perspective), or None if not in progress."""
    c = event["competitions"][0]
    st = c["status"]
    if st["type"]["state"] != "in":
        return None
    teams = {x["homeAway"]: x for x in c["competitors"]}
    h, a = teams["home"]["team"], teams["away"]["team"]
    hc = T.code_for(h.get("displayName", "")) or h["abbreviation"]
    ac = T.code_for(a.get("displayName", "")) or a["abbreviation"]
    period, clock = int(st.get("period") or 1), float(st.get("clock") or 0)
    sec_left = max(0.0, (4 - period) * 900 + clock) if period <= 4 else 0.0   # overtime counts as 0
    sit = c.get("situation") or {}
    pos_id = str(sit.get("possession") or "")
    home_ball = None if not pos_id else pos_id == str(h["id"])
    offense = h["abbreviation"] if home_ball else a["abbreviation"]
    odds = (c.get("odds") or [{}])[0]
    prob = (sit.get("lastPlay") or {}).get("probability") or {}
    return {"event_id": event["id"], "home": hc, "away": ac,
            "home_name": h.get("displayName"), "away_name": a.get("displayName"),
            "home_score": float(teams["home"].get("score") or 0), "away_score": float(teams["away"].get("score") or 0),
            "period": period, "clock": clock, "sec_left": sec_left, "detail": st["type"].get("shortDetail"),
            "home_ball": home_ball, "down": sit.get("down"), "distance": sit.get("distance"),
            "yardline_100": _yardline_100(sit.get("possessionText"), offense) if home_ball is not None else None,
            "home_to": sit.get("homeTimeouts"), "away_to": sit.get("awayTimeouts"),
            "home_line": odds.get("spread") if odds.get("spread") is not None else None,
            "espn_wp": prob.get("homeWinPercentage"), "last_play": (sit.get("lastPlay") or {}).get("text")}


def live_states():
    """Every NFL game in progress right now."""
    d, stamp = cached_get(SCOREBOARD, ttl=0, source="ESPN scoreboard")
    return [s for s in (parse_state(e) for e in d.get("events", [])) if s], stamp


def kalshi_prices(states):
    """{event_id: (home market, away market)} from Kalshi's live winner markets."""
    from api_clients import kalshi_markets
    from nfl import _match_kalshi
    from cache import fresh
    try:
        with fresh():   # live prices: never reuse the app's 5-minute Kalshi cache
            markets, _ = kalshi_markets("KXNFLGAME")
    except ApiError:
        return {}
    out = {}
    for s in states:
        k = _match_kalshi(markets, s["home"], s["away"])
        if k:
            out[s["event_id"]] = (k.get("home"), k.get("away"))
    return out


def pregame_spreads():
    """{(home code, away code): nflverse spread_line} for games within a day of now.
    spread_line is the pregame expected home margin (positive = home favored)."""
    import pandas as pd
    from nfl import load_games
    games, _ = load_games()
    day = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    g = games[(pd.to_datetime(games["gameday"]) - day).abs() <= pd.Timedelta(days=1)].dropna(subset=["spread_line"])
    return {(T.canon(h), T.canon(a)): float(x) for h, a, x in zip(g["home_team"], g["away_team"], g["spread_line"])}


def snapshot(model=None, spreads=None):
    """States + model + Kalshi for every live game."""
    states, stamp = live_states()
    spreads = spreads if spreads is not None else (pregame_spreads() if states else {})
    for st in states:
        st["spread"] = spreads.get((T.canon(st["home"]), T.canon(st["away"])))
    prices = kalshi_prices(states) if states else {}
    rows = []
    for s in states:
        hm, am = prices.get(s["event_id"], (None, None))
        mwp = win_prob(model, s) if model else None
        rows.append({**s, "model_wp": mwp,
                     "kalshi_home_bid": (hm or {}).get("yes_bid"), "kalshi_home_ask": (hm or {}).get("yes_ask"),
                     "kalshi_away_bid": (am or {}).get("yes_bid"), "kalshi_away_ask": (am or {}).get("yes_ask")})
    return rows, stamp


def record(minutes=240, every=60, path=None, model=None):
    """Append a snapshot of every live game each `every` seconds for `minutes`."""
    path = path or SNAP_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    end = time.time() + minutes * 60
    model = model or load_model()
    spreads = pregame_spreads()
    while time.time() < end:
        try:
            rows, _ = snapshot(model, spreads)
        except Exception as e:      # one bad poll must not end a 4-hour recording
            print("poll failed:", e, flush=True)
            rows = []
        if rows:
            new = not os.path.exists(path)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=SNAP_FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                for r in rows:
                    w.writerow({**r, "at": now})
            print(now, len(rows), "games", flush=True)
        time.sleep(every)


# ------------------------------------------------------------------ model --
# Logistic regression on every NFL play 2014-2022 (home perspective), tested on
# 2023-2025 against nflfastR's own Vegas-adjusted win probability. Features follow
# Stern (1991): the lead and the pregame spread matter in proportion to the time left.
MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_model.json")
TRAIN_SEASONS = list(range(2014, 2023))
TEST_SEASONS = [2023, 2024, 2025]
PBP_COLS = ["game_id", "season", "home_team", "posteam", "game_seconds_remaining", "qtr", "down", "ydstogo",
            "yardline_100", "score_differential", "posteam_timeouts_remaining", "defteam_timeouts_remaining",
            "spread_line", "result", "vegas_home_wp"]
FEATURES = ["const", "lead", "spread", "ball", "field", "togo", "timeouts", "late_lead"]


def load_plays(season):
    """One season's plays reduced to the columns the model needs (cached as a small CSV)."""
    import pandas as pd
    import config
    from nfl import PBP_URL
    small = os.path.join(config.CACHE_DIR, f"live_pbp_{season}.csv")
    if os.path.exists(small):
        return pd.read_csv(small)
    from cache import cached_download
    path, _ = cached_download(PBP_URL.format(season=season), ttl=None, source="nflverse play-by-play", suffix=".csv.gz")
    df = pd.read_csv(path, usecols=PBP_COLS, low_memory=False)
    df = df[df["posteam"].notna() & (df["qtr"] <= 4) & df["result"].notna() & (df["result"] != 0)]
    df.to_csv(small, index=False)
    os.remove(path)
    return df


def features(lead, sec_left, spread, ball, yardline_100, togo, timeout_diff):
    """Feature rows (numpy, home perspective). ball = +1 home has it, -1 away, 0 nobody.
    spread = pregame expected home margin (nflverse spread_line; positive = home favored)."""
    import numpy as np
    t = np.clip(np.asarray(sec_left, float) / 3600.0, 0, 1)
    root = np.sqrt(t + 1.0 / 60)                        # one-minute floor keeps the last play finite
    ball = np.asarray(ball, float)
    yl = np.nan_to_num(np.asarray(yardline_100, float), nan=50.0)
    tg = np.nan_to_num(np.asarray(togo, float), nan=10.0)
    lead = np.asarray(lead, float)
    return np.column_stack([np.ones_like(t), lead / root, np.asarray(spread, float) * np.sqrt(t), ball / root,
                            ball * (1 - yl / 100) / root, -ball * np.minimum(tg, 20) / 10 / root,
                            np.asarray(timeout_diff, float) * (1 - t) / root,
                            np.sign(lead) * (t < 2 / 60)])


def _frame_features(df):
    import numpy as np
    home_ball = np.where(df["posteam"] == df["home_team"], 1.0, -1.0)
    lead = df["score_differential"].to_numpy() * home_ball
    to_home = np.where(home_ball > 0, df["posteam_timeouts_remaining"], df["defteam_timeouts_remaining"])
    to_away = np.where(home_ball > 0, df["defteam_timeouts_remaining"], df["posteam_timeouts_remaining"])
    return features(lead, df["game_seconds_remaining"], df["spread_line"], home_ball, df["yardline_100"],
                    df["ydstogo"], np.nan_to_num(to_home - to_away))


def fit_logistic(X, y, iters=25, ridge=1e-6):
    """Plain Newton/IRLS logistic regression (no scikit-learn in requirements)."""
    import numpy as np
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + ridge * w
        H = (X * (p * (1 - p))[:, None]).T @ X + ridge * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def train(save=True):
    """Fit on TRAIN_SEASONS, score on TEST_SEASONS vs nflfastR. Returns the model dict."""
    import json
    import numpy as np
    import pandas as pd
    tr = pd.concat([load_plays(s) for s in TRAIN_SEASONS]).dropna(subset=["spread_line", "game_seconds_remaining"])
    te = pd.concat([load_plays(s) for s in TEST_SEASONS]).dropna(subset=["spread_line", "game_seconds_remaining"])
    w = fit_logistic(_frame_features(tr), (tr["result"] > 0).to_numpy(float))
    p = 1 / (1 + np.exp(-np.clip(_frame_features(te) @ w, -30, 30)))
    y = (te["result"] > 0).to_numpy(float)
    ref = te["vegas_home_wp"].to_numpy()
    ok = ~np.isnan(ref)
    brier = lambda q: float(np.mean((q[ok] - y[ok]) ** 2))
    ll = lambda q: float(-np.mean(y[ok] * np.log(np.clip(q[ok], 1e-6, 1)) + (1 - y[ok]) * np.log(np.clip(1 - q[ok], 1e-6, 1))))
    bins = []
    for lo in np.arange(0, 1, 0.1):
        m = (p >= lo) & (p < lo + 0.1)
        if m.sum():
            bins.append({"bin": round(float(lo), 1), "n": int(m.sum()), "pred": round(float(p[m].mean()), 3),
                         "actual": round(float(y[m].mean()), 3)})
    model = {"weights": dict(zip(FEATURES, map(float, w))), "train": TRAIN_SEASONS, "test": TEST_SEASONS,
             "plays_train": int(len(tr)), "plays_test": int(ok.sum()),
             "brier": round(brier(p), 4), "brier_nflfastr": round(brier(ref), 4),
             "logloss": round(ll(p), 4), "logloss_nflfastr": round(ll(ref), 4), "calibration": bins,
             "trained": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")}
    if save:
        with open(MODEL_FILE, "w") as f:
            json.dump(model, f, indent=1)
    return model


def load_model():
    import json
    try:
        with open(MODEL_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def win_prob(model, s):
    """Home win probability for one live state from live_states(), or None."""
    import numpy as np
    if not model or s.get("spread") is None:
        return None
    ball = 0.0 if s["home_ball"] is None else (1.0 if s["home_ball"] else -1.0)
    x = features([s["home_score"] - s["away_score"]], [s["sec_left"]], [s["spread"]], [ball],
                 [s["yardline_100"] if s["yardline_100"] is not None else 50], [s["distance"] or 10],
                 [(s["home_to"] or 0) - (s["away_to"] or 0)])
    w = np.array([model["weights"][k] for k in FEATURES])
    return float(1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))[0])


# ------------------------------------------------------------------ board --
# Fixed before any live results: a side is worth watching only with 5+ pts of edge
# after Kalshi's fee at a 10-90c price. A gap over 15 pts almost always means ESPN's
# state is behind the TV broadcast (a score or turnover it hasn't posted yet), so
# those are labelled "stale?" instead.
LIVE_MIN_EDGE = 0.05
LIVE_STALE_GAP = 0.15


def board(model=None):
    """Every live game: score, clock, model %, ESPN %, Kalshi bid/ask, and the better side's edge."""
    from kalshi import edge_after_fee
    model = model or load_model()
    rows, stamp = snapshot(model)
    out = []
    for r in rows:
        p = r["model_wp"]
        sides = []
        for side, q, ask in (("home", p, r["kalshi_home_ask"]), ("away", None if p is None else 1 - p, r["kalshi_away_ask"])):
            if q is not None and ask and 0 < ask < 1:
                sides.append({"side": side, "team": r[side], "p": q, "ask": ask, "edge": edge_after_fee(q, ask)})
        best = max(sides, key=lambda x: x["edge"]) if sides else None
        hb, ha = r["kalshi_home_bid"], r["kalshi_home_ask"]
        mid = (hb + ha) / 2 if hb is not None and ha else None
        gap = None if p is None or mid is None else p - mid
        call = "-"
        if best and gap is not None:
            if abs(gap) > LIVE_STALE_GAP:
                call = "stale?"
            elif best["edge"] >= LIVE_MIN_EDGE and 0.10 <= best["ask"] <= 0.90:
                call = "WATCH"
        out.append({**r, "kalshi_mid": mid, "gap": gap, "best": best, "call": call})
    return out, stamp
