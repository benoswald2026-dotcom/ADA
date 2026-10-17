"""Consensus picks: what independent forecasters expect, next to the prices.

Sources (all public, no API keys; each site's robots.txt allows these pages):
  - ESPN FPI matchup predictor: home win %, predicted margin
  - Dratings: win %, projected score (so a margin and a total)
  - Sagarin "predictor" ratings: margin = home - away + home advantage
  - Dimers: win %, projected score
  - OddsShark computer picks: projected score
  - Sonny Moore power ratings: margin = home - away + the home advantage the page states
  - TeamRankings predictive ratings: margin = home - away + Sagarin's home advantage
  - nfelo (NFL only): the model's spread
  - Hashmark's own model: NFL always; college only with CFBD_API_KEY
Markets for comparison: DraftKings (via ESPN) and Kalshi.

For each game: consensus winner and win %, consensus margin vs the spread
(against-the-spread pick), consensus total vs the over/under, how many
sources agree, and the Kalshi edge if the consensus is right.

Tiers are fixed rules written down before any grading (see TIERS). The
consensus is NOT validated yet: every pick is logged to
data/consensus_picks.csv and `python main.py picks --grade` scores them
against final scores, so the record decides whether to trust it.
Read-only: nothing here places a bet.
"""
import csv
import html as _html_mod
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

import config
import nfl_teams as T
from cache import ApiError, cached_download, cached_get
from kalshi import edge_after_fee
from names import normalize, team_key
from odds import remove_vig
from statistics import NormalDist

from strategy import normal_cdf

HEADERS = {"User-Agent": "Mozilla/5.0 (Hashmark; personal, read-only)"}
SPORTS = {
    "cfb": {"espn": "college-football", "dratings": "https://www.dratings.com/predictor/ncaa-football-predictions/",
            "sagarin": "http://sagarin.com/sports/cfsend.htm", "dimers": "https://www.dimers.com/cfb/predictions",
            "oddsshark": "https://www.oddsshark.com/ncaaf/computer-picks",
            "sonny": "https://sonnymoorepowerratings.com/col-foot.htm",
            "teamrankings": "https://www.teamrankings.com/college-football/ranking/predictive-by-other", "sigma": 16.0},
    "nfl": {"espn": "nfl", "dratings": "https://www.dratings.com/predictor/nfl-football-predictions/",
            "sagarin": "http://sagarin.com/sports/nflsend.htm", "dimers": "https://www.dimers.com/nfl/predictions",
            "oddsshark": "https://www.oddsshark.com/nfl/computer-picks",
            "sonny": "https://sonnymoorepowerratings.com/nfl-foot.htm",
            "teamrankings": "https://www.teamrankings.com/nfl/ranking/predictive-by-other",
            "nfelo": "https://www.nfeloapp.com/games/", "sigma": 13.0},
}
# Fixed before grading. gap = consensus margin vs the line (or total vs the total), in points.
TIERS = {
    "cfb": {"strong_gap": 3.0, "lean_gap": 1.5, "total_strong": 5.0, "total_lean": 3.0},
    "nfl": {"strong_gap": 2.0, "lean_gap": 1.0, "total_strong": 3.0, "total_lean": 2.0},
}
BLOWOUT_LINE = 28.0   # spreads this big can be at most "Lean" (fixed before grading)
PICKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "consensus_picks.csv")
# Injuries and weather (fixed before testing). Pure rating systems can't know about
# injuries or weather; market-based sources and the betting line already do.
QB_OUT_POINTS = {"cfb": 7.0, "nfl": 5.0}       # starting QB (season passing leader) fully out
STATUS_WEIGHT = {"out": 1.0, "injured reserve": 1.0, "doubtful": 0.75, "questionable": 0.5}
INJURY_ADJUSTED_SOURCES = {"Sagarin", "Dratings", "Sonny Moore", "TeamRankings"}
# TeamRankings publishes no home-field number; it borrows Sagarin's current one, else these.
DEFAULT_HFA = {"cfb": 2.8, "nfl": 1.8}
WIND_PER_MPH = -0.35       # points of total per mph of sustained wind above 10 (fitted on the NFL)
GUST_TO_WIND = 1.4         # ESPN gives gusts; sustained wind is roughly gust / 1.4
# Checked 2026-10-17: in windy games (gusts 25+ mph) these sites' totals sat as close to
# DraftKings' total as in calm games, so they (or the line) already reflect the weather.
# Adjusting them would double-count; weather is shown, and Hashmark's NFL model uses it itself.
WEATHER_ADJUSTED_SOURCES = set()
COVERS_CFB_INJURIES = "https://www.covers.com/sport/football/ncaaf/injuries"
PRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "consensus_predictions.csv")
WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "consensus_weights.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history_fpi_{sport}.csv")
PRED_FIELDS = ["logged_at", "sport", "event_id", "game_date", "kickoff", "matchup", "home", "away",
               "home_line", "total_line", "src_margins", "src_totals"]
# Learning (fixed before any results): every learned number starts at its default and
# moves toward what the graded games say only as games pile up, like a 40-game prior.
PRIOR_GAMES = 40
MIN_TRUST = 0.25   # a source below this trust no longer votes on Strong/Lean
# Where a source's trust starts before it has results. It was 1.0 (take every forecaster at
# face value). Changed 2026-10-17: the only source tested against the line, ESPN FPI, came
# out at 0 (513 college + 172 NFL games), and the first day at 1.0 went badly. A source now
# has to show in graded games that its disagreements with the line come true before it
# moves a pick or votes. A full college Saturday grades ~50 games.
TRUST_PRIOR = 0.0
PICK_FIELDS = ["logged_at", "sport", "event_id", "game_date", "matchup", "home", "away", "bet_type", "pick",
               "line", "consensus", "tier", "sources", "agree"]


# ------------------------------------------------------------------ sources --

def _html(url, source):
    path, stamp = cached_download(url, ttl=config.TTL_ODDS, source=source, suffix=".html", headers=HEADERS)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(), stamp


def parse_dratings(html):
    """Dratings predictor table -> [{away, home, p_away, p_home, s_away, s_home, total}] (away listed first)."""
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        names = re.findall(r'/teams/[^"]+">([^<]+)</a>', row)
        pcts = re.findall(r'>([\d.]+)%<', row)
        score = re.search(r'<td class="table-division">([\d.]+)<br\s*/?>([\d.]+)</td>\s*<td>([\d.]+)</td>', row)
        if len(names) < 2 or len(pcts) < 2:
            continue
        g = {"away": _html_mod.unescape(names[0]).strip(), "home": _html_mod.unescape(names[1]).strip(),
             "p_away": float(pcts[0]) / 100, "p_home": float(pcts[1]) / 100}
        if score:
            g.update(s_away=float(score.group(1)), s_home=float(score.group(2)), total=float(score.group(3)))
        out.append(g)
    return out


def parse_sagarin(html):
    """Sagarin ratings page -> ({team key: predictor rating}, predictor home advantage)."""
    text = re.sub(r"<[^>]+>", "", html)
    hfa = 0.0
    m = re.search(r"HOME ADVANTAGE=\[\s*([\d.]+)\]\s*\[\s*([\d.]+)\]", text)
    if m:
        hfa = float(m.group(2))     # second bracket = PREDICTOR column
    ratings = {}
    line_re = re.compile(r"^\s*\d+\s+(.+?)\s+(?:A{1,2}\s+)?=\s+[\d.]+\s+\d+\s+\d+(?:\s+\d+)?\s+[\d.]+\(\s*\d+\)"
                         r".*?\|.*?\|\s+([\d.]+)\s+\d+\s+\|", re.M)
    for name, pred in line_re.findall(text):
        k = team_key(name)
        ratings.setdefault(k, float(pred))   # the page lists teams twice; keep the first
    return ratings, hfa


def parse_sonny_moore(html):
    """Sonny Moore power ratings page -> ({team key: PR}, home advantage the page states)."""
    text = re.sub(r"<[^>]+>", "", html)
    m = re.search(r"add\s+([\d.]+):?\s+points? to the home team", text)
    hfa = float(m.group(1)) if m else 0.0
    ratings = {}
    for name, pr in re.findall(r"^\s*\d+\s+(.+?)\s+\d+\s+\d+\s+\d+\s+[\d.]+\s+([\d.]+)\s*$", text, re.M):
        ratings.setdefault(team_key(name), float(pr))   # ranked list first, then an A-Z repeat
    return ratings, hfa


def parse_teamrankings(html):
    """TeamRankings predictive ratings -> {team key: points better than an average team,
    neutral field}. The sort key holds the unrounded value plus 500."""
    ratings = {}
    for name, val in re.findall(r'<td class="nowrap" data-sort="([^"]+)">.*?</td>\s*'
                                r'<td class="text-right" data-sort="(-?[\d.]+)">', html, re.S):
        ratings.setdefault(team_key(_html_mod.unescape(name)), float(val) - 500)
    return ratings


def parse_nfelo(html):
    """nfelo game cards -> [{away, home, margin}] with codes as the names. Each team's row
    reads code, record, open line, price, current line, price, MODEL line, value."""
    out = []
    for card in re.split(r'aria-label="View game details for ', html)[1:]:
        head = re.match(r"([A-Z]{2,3}) at ([A-Z]{2,3})\"", card)
        if not head:
            continue
        away, home = head.groups()
        tok = [t.strip() for t in re.findall(r">([^<]+)<", card.split("aria-label=")[0]) if t.strip()]
        try:
            ia, ih = tok.index(away), tok.index(home)
            m_away, m_home = float(tok[ia + 6]), float(tok[ih + 6])
        except (ValueError, IndexError):
            continue
        if "Model" not in tok or abs(m_away + m_home) > 0.01:
            continue   # a finished game's card, or the rows don't mirror
        out.append({"away": away, "home": home, "margin": m_away})   # away's line = home margin
    return out


def parse_dimers(html):
    """Dimers predictions page (embedded JSON) -> [{away, home, p_home, s_away, s_home}], one per game."""
    out, seen = [], set()
    pat = re.compile(r'"AwayTeam":\{[^}]*?"DisplayName":"([^"]+)".*?"HomeTeam":\{[^}]*?"DisplayName":"([^"]+)"'
                     r'.*?"PreData":\{([^}]*)\}', re.S)
    for away, home, pre in pat.findall(html):
        if (away, home) in seen:
            continue
        num = lambda k: (m := re.search(rf'"{k}":(-?[\d.]+)', pre)) and float(m.group(1))
        if num("PythagHome") is None or num("PredHomeScore") is None:
            continue
        seen.add((away, home))
        out.append({"away": _html_mod.unescape(away), "home": _html_mod.unescape(home), "p_home": num("PythagHome"),
                    "s_away": num("PredAwayScore"), "s_home": num("PredHomeScore")})
    return out


def parse_oddsshark(html):
    """OddsShark computer picks -> [{away, home, away_abv, home_abv, s_away, s_home}] (names from team links)."""
    out = []
    for b in html.split('class="game-prediction-card-matchup"')[1:]:
        b = b[:6000]
        slugs = dict((side, slug) for slug, side in
                     re.findall(r"href='[^']*/teams/main/([a-z0-9-]+)' class='team-anchor team-anchor-(away|home)'", b))
        abv = re.findall(r'alt="([A-Za-z0-9&]+) logo"', b)
        sc = re.findall(r'</span>\s*([\d.]+)\s*</span>', b)
        if "away" not in slugs or "home" not in slugs or len(sc) < 2:
            continue
        out.append({"away": slugs["away"].replace("-", " "), "home": slugs["home"].replace("-", " "),
                    "away_abv": abv[0] if abv else "", "home_abv": abv[1] if len(abv) > 1 else "",
                    "s_away": float(sc[0]), "s_home": float(sc[1])})
    return out


def espn_fpi(sport, event_id):
    """(home win prob, predicted home margin) from ESPN's matchup predictor, or None."""
    lg = SPORTS[sport]["espn"]
    url = f"https://sports.core.api.espn.com/v2/sports/football/leagues/{lg}/events/{event_id}/competitions/{event_id}/predictor"
    try:
        d, _ = cached_get(url, ttl=config.TTL_ODDS, source="ESPN FPI")
    except ApiError:
        return None
    st = {s["name"]: s.get("value") for s in (d.get("homeTeam") or {}).get("statistics", [])}
    if st.get("gameProjection") is None or st.get("teamPredPtDiff") is None:
        return None
    return float(st["gameProjection"]) / 100, float(st["teamPredPtDiff"])


def parse_covers_injuries(html):
    """Covers college injury report -> {team key: [(player, position, status)]}.
    Player names are abbreviated ('L. Szarka'); status is the part before ' - '."""
    out = {}
    for chunk in html.split('<a id="')[1:]:
        team = _html_mod.unescape(chunk.split('"', 1)[0])
        rows = re.findall(r"<span class='player-link'>\s*(.*?)\s*</span>.*?<td>([A-Z]+)</td>\s*<td><b>([^<]+)</b>",
                          chunk, re.S)
        if rows:
            out[team_key(team)] = [(re.sub(r"\s+", " ", n).strip(), pos, st.split(" - ")[0].strip().lower())
                                   for n, pos, st in rows]
    return out


def _same_player(full, short):
    """'Luke Szarka' vs 'L. Szarka' (or the full name)."""
    f, s_ = normalize(full).split(), normalize(short).replace(".", "").split()
    if not f or not s_:
        return False
    return f[-1] == s_[-1] and f[0][0] == s_[0][0]


def game_context(sport, g, covers):
    """Weather and starting-QB injury status for one game, from ESPN's game summary
    (weather, season passing leaders, NFL injuries) and Covers (college injuries)."""
    lg = SPORTS[sport]["espn"]
    ctx = {"weather": None, "wind_adj": 0.0, "qb": {"home": None, "away": None}, "qb_pts": {"home": 0.0, "away": 0.0}}
    try:
        sm, _ = cached_get(f"https://site.api.espn.com/apis/site/v2/sports/football/{lg}/summary",
                           params={"event": g["id"]}, ttl=config.TTL_ODDS, source="ESPN summary")
    except ApiError:
        return ctx
    indoor = bool(((sm.get("gameInfo") or {}).get("venue") or {}).get("indoor")) or g.get("indoor", False)
    w = (sm.get("gameInfo") or {}).get("weather")
    if w and not indoor:
        gust = w.get("gust")
        wind = None if gust is None else gust / GUST_TO_WIND
        ctx["weather"] = {"temp": w.get("temperature"), "gust": gust, "wind": wind, "precip": w.get("precipitation")}
        if wind is not None:
            ctx["wind_adj"] = WIND_PER_MPH * max(0.0, wind - config.WIND_THRESHOLD_MPH)
    elif indoor:
        ctx["weather"] = {"indoor": True}
    leaders = {}
    for t in sm.get("leaders", []):
        side = "home" if str(t.get("team", {}).get("id")) == str(g["home"].get("id")) else "away"
        for cat in t.get("leaders", []):
            if cat.get("name") == "passingYards" and cat.get("leaders"):
                leaders[side] = cat["leaders"][0].get("athlete", {}).get("displayName")
    if sport == "nfl":
        inj = {("home" if str(t.get("team", {}).get("id")) == str(g["home"].get("id")) else "away"):
               [(x.get("athlete", {}).get("displayName", ""), x.get("athlete", {}).get("position", {}).get("abbreviation", ""),
                 str(x.get("status", "")).lower()) for x in t.get("injuries", [])]
               for t in sm.get("injuries", [])}
    else:
        inj = {side: next((covers[k] for k in _names(g[side]) if k in covers), []) for side in ("home", "away")}
    for side in ("home", "away"):
        qb = leaders.get(side)
        if not qb:
            continue
        hit = next((st for n, pos, st in inj.get(side, []) if pos == "QB" and _same_player(qb, n)), None)
        ctx["qb"][side] = {"name": qb, "status": hit}
        if hit in STATUS_WEIGHT:
            ctx["qb_pts"][side] = QB_OUT_POINTS[sport] * STATUS_WEIGHT[hit]
    return ctx


def _apply_context(src, ctx, sigma):
    """Adjust rating-only sources for a starting QB out, and totals for wind."""
    shift = ctx["qb_pts"]["away"] - ctx["qb_pts"]["home"]     # home margin rises if the away QB is out
    for name, s in src.items():
        if shift and name in INJURY_ADJUSTED_SOURCES and "margin" in s:
            s["margin"] += shift
            p = min(max(s["p"], 1e-4), 1 - 1e-4)
            s["p"] = NormalDist().cdf(NormalDist().inv_cdf(p) + shift / sigma)
        if ctx["wind_adj"] and name in WEATHER_ADJUSTED_SOURCES and "total" in s:
            s["total"] += ctx["wind_adj"]
    return src


def _names(team):
    """Every key a source might use for an ESPN team: location, full display name, abbreviation."""
    out = {team_key(team["location"]), normalize(team.get("displayName", "")), team_key(team.get("displayName", ""))}
    if team.get("abbreviation"):
        out.add("abv:" + team["abbreviation"].lower())
    return out


def _keys(name):
    """Ways to match a source's team name: as written, through the alias table, and with
    a one- or two-word mascot removed ('ULM Warhawks' -> 'ulm' -> 'ul monroe')."""
    n = normalize(name)
    words = n.split()
    out = {n, team_key(n)}
    for cut in (1, 2):
        if len(words) > cut:
            out.add(team_key(" ".join(words[:-cut])))
    return out


def _find(rows, hk, ak, abv=False):
    """The row whose home AND away names both match an ESPN game's teams. Requiring both
    keeps the mascot-stripping fallback from pairing the wrong game."""
    for x in rows:
        h = _keys(x["home"]) | ({"abv:" + x["home_abv"].lower()} if abv and x.get("home_abv") else set())
        a = _keys(x["away"]) | ({"abv:" + x["away_abv"].lower()} if abv and x.get("away_abv") else set())
        if h & hk and a & ak:
            return x
    return None


def _model_rows(sport, days):
    """Hashmark's own predictions: {(home key, away key): (p_home, margin, total)}. NFL only
    unless CFBD_API_KEY is set (the college model needs CollegeFootballData)."""
    out = {}
    try:
        if sport == "nfl":
            from nfl import build_slate
            _, rows = build_slate(days=days)
            for r in rows:
                p = r["pred"]
                key = (normalize(T.full_name(r["game"]["home_code"])), normalize(T.full_name(r["game"]["away_code"])))
                out[key] = (p["raw_home_prob"], p["margin"], p.get("total"))
        elif os.environ.get("CFBD_API_KEY", "").strip():
            from week import build_week
            _, rows = build_week()
            for r in rows:
                p = r["pred"]
                out[(team_key(r["game"]["home"]), team_key(r["game"]["away"]))] = (p["home_prob"], p["margin"], p.get("total"))
    except Exception:   # the consensus still works from the outside sources
        return {}
    return out


# ---------------------------------------------------------------- consensus --

def tier_for(gap, n, agree, strong, lean):
    """Fixed rule. Strong: 3+ sources, all agree (with 5+ sources, at least 80%),
    |gap| >= strong. Lean: 2+ sources, at least 2/3 agree, |gap| >= lean."""
    need = n if n < 5 else math.ceil(0.8 * n)
    if n >= 3 and agree >= need and abs(gap) >= strong:
        return "Strong"
    if n >= 2 and agree >= math.ceil(2 * n / 3) and abs(gap) >= lean:
        return "Lean"
    return None


def build(sport="cfb", days=0, now=None, with_model=True):
    """Returns {"games": [...], "notes": [...], "stamps": {...}}."""
    from scan import _match, espn_events, kalshi_lines, parse_event
    from api_clients import kalshi_markets
    cfg, tiers = SPORTS[sport], TIERS[sport]
    now = now or datetime.now(timezone.utc)
    notes, stamps = [], {}
    try:
        dr_html, stamps["dratings"] = _html(cfg["dratings"], "Dratings")
        dratings = parse_dratings(dr_html)
    except ApiError as e:
        dratings = []
        notes.append(f"Dratings unavailable ({e}).")
    try:
        sg_html, stamps["sagarin"] = _html(cfg["sagarin"], "Sagarin")
        sagarin, sag_hfa = parse_sagarin(sg_html)
    except ApiError as e:
        sagarin, sag_hfa = {}, 0.0
        notes.append(f"Sagarin unavailable ({e}).")
    ratings = {"Sagarin": (sagarin, sag_hfa)}
    try:
        page, stamps["sonny moore"] = _html(cfg["sonny"], "Sonny Moore")
        ratings["Sonny Moore"] = parse_sonny_moore(page)
    except ApiError as e:
        notes.append(f"Sonny Moore unavailable ({e}).")
    try:
        page, stamps["teamrankings"] = _html(cfg["teamrankings"], "TeamRankings")
        ratings["TeamRankings"] = (parse_teamrankings(page), sag_hfa or DEFAULT_HFA[sport])
    except ApiError as e:
        notes.append(f"TeamRankings unavailable ({e}).")
    if sport == "nfl":   # NFL ratings pages use city or full names; match on team codes
        ratings = {n: ({T.code_for(k) or k: v for k, v in r.items()}, hfa) for n, (r, hfa) in ratings.items()}
    nfelo = {}
    if sport == "nfl":
        try:
            page, stamps["nfelo"] = _html(cfg["nfelo"], "nfelo")
            nfelo = {(T.canon(x["home"]), T.canon(x["away"])): x["margin"] for x in parse_nfelo(page)}
        except ApiError as e:
            notes.append(f"nfelo unavailable ({e}).")
    extra = {}
    for name, parser in (("Dimers", parse_dimers), ("OddsShark", parse_oddsshark)):
        try:
            page, stamps[name.lower()] = _html(cfg[name.lower()], name)
            extra[name] = parser(page)
        except ApiError as e:
            extra[name] = []
            notes.append(f"{name} unavailable ({e}).")
    covers = {}
    if sport == "cfb":
        try:
            page, stamps["covers"] = _html(COVERS_CFB_INJURIES, "Covers injuries")
            covers = parse_covers_injuries(page)
        except ApiError as e:
            notes.append(f"College injury report (Covers) unavailable ({e}); no QB injury adjustments.")
    ladders = {}
    for kind, series in (("spread", "KXNCAAFSPREAD" if sport == "cfb" else "KXNFLSPREAD"),
                         ("total", "KXNCAAFTOTAL" if sport == "cfb" else "KXNFLTOTAL")):
        try:
            ms, _ = kalshi_markets(series)
        except ApiError:
            ms = []
        for m in ms:
            ladders.setdefault((kind, m["event_ticker"].split("-", 1)[-1]), []).append(m)
    from scan import ladder_key
    spread_by_key = {gk: ms for (kind, gk), ms in ladders.items() if kind == "spread"}
    try:
        markets, stamps["kalshi"] = kalshi_markets("KXNCAAFGAME" if sport == "cfb" else "KXNFLGAME")
    except ApiError:
        markets = []
    model = _model_rows(sport, max(days, 0) + (3 if sport == "nfl" else 0)) if with_model else {}
    if sport == "cfb" and not model:
        notes.append("Hashmark's own college model is off (no CFBD_API_KEY); the consensus uses the outside forecasters only.")

    learned = load_weights().get(sport, {})
    games, unmatched = [], {"Dratings": 0, "Dimers": 0, "OddsShark": 0, "nfelo": 0, **{n: 0 for n in ratings}}
    for d in range(days + 1):
        events, stamps["espn"] = espn_events(sport, (now + timedelta(days=d)).strftime("%Y%m%d"))
        for e in events:
            g = parse_event(e)
            if g["state"] != "pre" or g["kickoff"] <= now:
                continue
            h, a = g["home"], g["away"]
            hk, ak = _names(h), _names(a)
            src = {}
            fpi = espn_fpi(sport, g["id"])
            if fpi:
                src["ESPN FPI"] = {"p": fpi[0], "margin": fpi[1]}
            dr = _find(dratings, hk, ak)
            if dr:
                s = {"p": dr["p_home"]}
                if "s_home" in dr:
                    s.update(margin=dr["s_home"] - dr["s_away"], total=dr["total"])
                src["Dratings"] = s
            elif dratings:
                unmatched["Dratings"] += 1
            neutral = bool((e["competitions"][0].get("neutralSite")))
            if sport == "nfl":
                hk, ak = hk | {T.code_for(h["displayName"])}, ak | {T.code_for(a["displayName"])}
            for name, (table, hfa) in ratings.items():
                rh = next((table[k] for k in hk if k in table), None)
                ra = next((table[k] for k in ak if k in table), None)
                if rh is not None and ra is not None:
                    m = rh - ra + (0.0 if neutral else hfa)
                    src[name] = {"p": normal_cdf(m / cfg["sigma"]), "margin": m}
                elif table:
                    unmatched[name] += 1
            if nfelo:
                m = nfelo.get((T.code_for(h["displayName"]), T.code_for(a["displayName"])))
                if m is not None:
                    src["nfelo"] = {"p": normal_cdf(m / cfg["sigma"]), "margin": m}
                else:
                    unmatched["nfelo"] += 1
            for name, rows in extra.items():
                x = _find(rows, hk, ak, abv=(name == "OddsShark"))
                if x:
                    src[name] = {"p": x["p_home"] if "p_home" in x else normal_cdf((x["s_home"] - x["s_away"]) / cfg["sigma"]),
                                 "margin": x["s_home"] - x["s_away"], "total": x["s_home"] + x["s_away"]}
                elif rows:
                    unmatched[name] += 1
            mk = next((model[(x, y)] for x in hk for y in ak if (x, y) in model), None)
            if mk:
                src["Hashmark model"] = {"p": mk[0], "margin": mk[1], **({"total": mk[2]} if mk[2] else {})}
            if not src:
                continue
            g["indoor"] = bool((e["competitions"][0].get("venue") or {}).get("indoor"))
            ctx = game_context(sport, g, covers)
            src = _apply_context(src, ctx, cfg["sigma"])
            km = _match(sport, markets, g) if markets else None
            lad = None
            if sport == "cfb":
                names, keyfn = (h["location"], a["location"]), team_key
            else:
                names, keyfn = (h["displayName"], a["displayName"]), T.code_for
            gk = ladder_key(spread_by_key, *names, g["kickoff"], keyfn)
            if gk:
                lad = kalshi_lines(*names, ladders.get(("spread", gk), []), ladders.get(("total", gk), []), keyfn)
            gm = _summarize(sport, g, e, src, tiers, km, learned)
            gm["context"] = ctx
            _add_kalshi(gm, lad, cfg["sigma"], sport)
            games.append(gm)
    for name, n in unmatched.items():
        if n:
            notes.append(f"{name}: no prediction found for {n} games (team names or not covered).")
    games.sort(key=lambda x: x["kickoff"])
    return {"games": games, "notes": notes, "stamps": stamps, "sport": sport, "learned": learned}


def _wmean(values, weights):
    """Weighted mean; sources without a learned weight count 1.0 (relative to the average)."""
    if not values:
        return None
    w = {k: weights.get(k, 1.0) for k in values}
    return sum(values[k] * w[k] for k in values) / sum(w.values())


def _summarize(sport, g, e, src, tiers, km, learned=None):
    learned = learned or {}
    h, a = g["home"]["location"], g["away"]["location"]
    ps = [s["p"] for s in src.values()]
    margins = {k: s["margin"] for k, s in src.items() if "margin" in s}
    totals = {k: s["total"] for k, s in src.items() if "total" in s}
    p_home = sum(ps) / len(ps)
    mw, tw = learned.get("margin_w", {}), learned.get("total_w", {})
    trust, trust_t = learned.get("trust_src", {}), learned.get("trust_total_src", {})
    margin = _wmean(margins, mw)
    total = _wmean(totals, tw)
    out = {"event_id": g["id"], "kickoff": g["kickoff"], "home": h, "away": a, "matchup": f"{a} @ {h}",
           "sources": {k: {kk: round(v, 4) for kk, v in s.items()} for k, s in src.items()},
           "n_sources": len(src), "p_home": p_home, "margin": margin, "total": total,
           "winner": h if p_home >= 0.5 else a, "winner_p": max(p_home, 1 - p_home),
           "winner_agree": sum((s["p"] >= 0.5) == (p_home >= 0.5) for s in src.values()),
           "ats": None, "ou": None, "ml": None}
    # Against the spread: home covers if margin + home line > 0.
    if g["home_spread"] and margin is not None:
        line = g["home_spread"][0]
        gap_raw = margin + line
        # Learned: each source's disagreement with the line counts only as much as it
        # has shown up in results (its trust); untrusted sources neither move the gap nor vote.
        gap = _wmean({k: trust.get(k, TRUST_PRIOR) * (m + line) for k, m in margins.items()}, mw)
        voters = {k: m for k, m in margins.items() if trust.get(k, TRUST_PRIOR) >= MIN_TRUST}
        side_home = gap > 0
        agree = sum((m + line > 0) == side_home for m in voters.values())
        pick = f"{h} {line:+g}" if side_home else f"{a} {-line:+g}"
        tier = tier_for(gap, len(voters), agree, tiers["strong_gap"], tiers["lean_gap"])
        if tier == "Strong" and abs(line) >= BLOWOUT_LINE:
            tier = "Lean"   # rating systems are least reliable in huge mismatches
        out["ats"] = {"line": line, "gap": gap, "gap_raw": gap_raw, "pick": pick, "agree": agree,
                      "n": len(voters), "tier": tier}
    # Over/under.
    if g["over"] and total is not None:
        line = g["over"][0]
        gap = _wmean({k: trust_t.get(k, TRUST_PRIOR) * (t - line) for k, t in totals.items()}, tw)
        voters = {k: t for k, t in totals.items() if trust_t.get(k, TRUST_PRIOR) >= MIN_TRUST}
        agree = sum((t > line) == (gap > 0) for t in voters.values())
        out["ou"] = {"line": line, "gap": gap, "pick": f"{'Over' if gap > 0 else 'Under'} {line:g}", "agree": agree,
                     "n": len(voters), "tier": tier_for(gap, len(voters), agree, tiers["total_strong"], tiers["total_lean"])
                     if len(voters) >= 2 else ("Lean" if voters and abs(gap) >= tiers["total_strong"] else None)}
    # Moneyline: consensus vs DraftKings fair and the Kalshi ask for the consensus winner.
    if g["home_ml"] is not None and g["away_ml"] is not None:
        dk_home = remove_vig(g["home_ml"], g["away_ml"])[0]
        side = "home" if p_home >= 0.5 else "away"
        p = p_home if side == "home" else 1 - p_home
        ml = {"dk_fair": dk_home if side == "home" else 1 - dk_home, "p": p}
        ask = km[side]["yes_ask"] if km and km.get(side) else None
        if ask and 0 < ask < 1:
            ml.update(kalshi_ask=ask, kalshi_edge=edge_after_fee(p, ask))
        out["ml"] = ml
    return out


def _add_kalshi(gm, lad, sigma, sport):
    """Kalshi's own spread and total (read off its ladders) and, for the consensus spread
    and total picks, the Kalshi contract that is the same bet at the closest strike."""
    from scan import matching_contract
    gm["kalshi"] = None
    if not lad:
        return
    k = {"home_line": lad["home_line"], "total": lad["total"], "spread_bet": None, "total_bet": None}
    ats, ou = gm["ats"], gm["ou"]
    if ats and lad["spread"]:
        k["spread_bet"] = matching_contract(lad["spread"], "spread", ats["gap"] > 0, -ats["line"])
    if ou and lad["totals"]:
        k["total_bet"] = matching_contract(lad["totals"], "total", ou["gap"] > 0, ou["line"])
    gm["kalshi"] = k


def best_bets(result):
    """Strong and Lean spread / total picks, strongest first."""
    rank = {"Strong": 0, "Lean": 1}
    picks = []
    for gm in result["games"]:
        for kind in ("ats", "ou"):
            x = gm[kind]
            if x and x["tier"]:
                picks.append({"game": gm, "type": "Spread" if kind == "ats" else "Total", **x})
    picks.sort(key=lambda p: (rank[p["tier"]], -abs(p["gap"])))
    return picks


LOCK_WINDOW_HOURS = 36   # the lock comes from games kicking off within this window


def lock_of_day(cands, now=None, window_hours=LOCK_WINDOW_HOURS):
    """The single best Strong pick across sports, or None. Each candidate needs tier, gap,
    agree, n, sport and kickoff (aware datetime). Gaps are compared as multiples of that
    sport's Strong bar (a 4-pt college gap = a 2.7-pt NFL gap), then by agreement."""
    now = now or datetime.now(timezone.utc)
    end = now + timedelta(hours=window_hours)
    ok = [c for c in cands if c["tier"] == "Strong" and now < c["kickoff"] <= end]
    if not ok:
        return None
    bar = lambda c: TIERS[c["sport"]]["strong_gap" if c["type"] == "Spread" else "total_strong"]
    return max(ok, key=lambda c: (abs(c["gap"]) / bar(c), c["agree"] / max(c["n"], 1), -c["kickoff"].timestamp()))


LOCKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "locks.csv")


def log_lock(lock, path=None, now=None):
    """Keep one lock per Eastern day in data/locks.csv (same columns as the picks log, so
    grade() scores it). Before that day's lock kicks off, a newer choice replaces it."""
    from zoneinfo import ZoneInfo
    path = path or LOCKS_FILE
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    mine = [i for i, r in enumerate(rows) if r["logged_at"][:10] == day]
    if mine:
        r = rows[mine[0]]
        kick = datetime.strptime(r["game_date"] + r["kickoff_utc"], "%Y%m%d%H:%M").replace(tzinfo=timezone.utc)
        if kick <= now:
            return False   # today's lock has kicked off: it stays
    row = {"logged_at": day, "sport": lock["sport"], "event_id": lock["event_id"],
           "game_date": lock["kickoff"].strftime("%Y%m%d"), "kickoff_utc": lock["kickoff"].strftime("%H:%M"),
           "matchup": lock["matchup"], "home": lock["home"], "away": lock["away"], "bet_type": lock["type"],
           "pick": lock["pick"], "line": lock["line"], "consensus": round(lock["gap"], 2), "tier": "Lock",
           "sources": lock["n"], "agree": lock["agree"]}
    if mine:
        rows[mine[0]] = row
    else:
        rows.append(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PICK_FIELDS + ["kickoff_utc"])
        w.writeheader()
        w.writerows(rows)
    return True


# ----------------------------------------------------------------- tracking --

def log_picks(result, path=None):
    """Record today's Strong/Lean picks for later grading. One row per game, type and
    day; running again the same day replaces that day's row (latest pre-game view)."""
    path = path or PICKS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    index = {(r["logged_at"][:10], r["event_id"], r["bet_type"]): i for i, r in enumerate(rows)}
    added = 0
    for p in best_bets(result):
        gm = p["game"]
        if gm["kickoff"] <= now:
            continue   # never log or change a pick once the game has started
        row = {"logged_at": now.strftime("%Y-%m-%d %H:%MZ"), "sport": result["sport"],
               "event_id": gm["event_id"], "game_date": gm["kickoff"].strftime("%Y%m%d"),
               "matchup": gm["matchup"], "home": gm["home"], "away": gm["away"], "bet_type": p["type"],
               "pick": p["pick"], "line": p["line"],
               "consensus": round(gm["margin"] if p["type"] == "Spread" else gm["total"], 2),
               "tier": p["tier"], "sources": p["n"], "agree": p["agree"]}
        key = (today, gm["event_id"], p["type"])
        if key in index:
            rows[index[key]] = row
        else:
            index[key] = len(rows)
            rows.append(row)
            added += 1
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PICK_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return added


def grade_pick(p, home_pts, away_pts):
    """'W', 'L' or 'P' for one logged pick given the final score."""
    line = float(p["line"])
    if p["bet_type"] == "Spread":
        took_home = p["pick"].startswith(p["home"] + " ")
        margin = (home_pts - away_pts) if took_home else (away_pts - home_pts)
        my_line = line if took_home else -line
        x = margin + my_line
    else:
        x = (home_pts + away_pts - line) * (1 if p["pick"].startswith("Over") else -1)
    return "W" if x > 0 else ("P" if x == 0 else "L")


def _finals(pairs):
    """{event_id: (home pts, away pts)} for finished games on the given (sport, YYYYMMDD) days."""
    from scan import espn_events
    finals = {}
    for sport, day in sorted(pairs):
        try:
            events, _ = espn_events(sport, day)
        except ApiError:
            continue
        for e in events:
            c = e["competitions"][0]
            if c["status"]["type"]["state"] != "post":
                continue
            sc = {t["homeAway"]: float(t.get("score") or 0) for t in c["competitors"]}
            finals[e["id"]] = (sc["home"], sc["away"])
    return finals


def grade(path=None):
    """Score every logged pick whose game is final. Returns {"rows": [...], "record": {...}}."""
    path = path or PICKS_FILE
    if not os.path.exists(path):
        return {"rows": [], "record": {}}
    with open(path, newline="") as f:
        picks = list(csv.DictReader(f))
    finals = _finals({(p["sport"], p["game_date"]) for p in picks})
    rows, record = [], {}
    for p in picks:
        if p["event_id"] not in finals:
            continue
        res = grade_pick(p, *finals[p["event_id"]])
        rows.append({**p, "result": res, "score": "%g-%g" % (finals[p["event_id"]][1], finals[p["event_id"]][0])})
        for key in ("all", f"{p['bet_type']} {p['tier']}"):
            r = record.setdefault(key, {"W": 0, "L": 0, "P": 0})
            r[res] += 1
    return {"rows": rows, "record": record}


# ----------------------------------------------------------------- learning --

def log_predictions(result, path=None):
    """Save every game's per-source margins and totals plus the lines, before kickoff, so
    results can later show which sources are accurate. One row per game: rerunning before
    kickoff replaces it with the latest numbers; a started game is never changed."""
    path = path or PRED_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    index = {(r["sport"], r["event_id"]): i for i, r in enumerate(rows)}
    for gm in result["games"]:
        if gm["kickoff"] <= now:
            continue
        row = {"logged_at": now.strftime("%Y-%m-%d %H:%MZ"), "sport": result["sport"], "event_id": gm["event_id"],
               "game_date": gm["kickoff"].strftime("%Y%m%d"), "kickoff": gm["kickoff"].isoformat(),
               "matchup": gm["matchup"], "home": gm["home"], "away": gm["away"],
               "home_line": "" if not gm["ats"] else gm["ats"]["line"],
               "total_line": "" if not gm["ou"] else gm["ou"]["line"],
               "src_margins": json.dumps({k: v["margin"] for k, v in gm["sources"].items() if "margin" in v}),
               "src_totals": json.dumps({k: v["total"] for k, v in gm["sources"].items() if "total" in v})}
        key = (result["sport"], gm["event_id"])
        if key in index:
            rows[index[key]] = row
        else:
            index[key] = len(rows)
            rows.append(row)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PRED_FIELDS)
        w.writeheader()
        w.writerows(rows)


def load_weights(path=None):
    path = path or WEIGHTS_FILE
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fit(games):
    """Learn, for one target (margin or total), each source's weight and trust.

    games: [(sources {name: prediction}, actual value, line value)].
    Weight: 1 / (mean squared error vs the final result), shrunk toward the pooled
    error with PRIOR_GAMES pseudo-games, scaled so the average weight is 1.
    Trust: least-squares slope of (actual - line) on (source - line), i.e. how much
    of the source's disagreement with the line actually showed up in results, shrunk
    toward TRUST_PRIOR the same way and kept between 0 and 1.2. A
    source whose gaps don't predict anything ends up near 0 and stops creating picks."""
    errs, sxy, sxx = {}, {}, {}
    for src, act, line in games:
        for k, v in src.items():
            errs.setdefault(k, []).append((act - v) ** 2)
            sxy[k] = sxy.get(k, 0.0) + (v - line) * (act - line)
            sxx[k] = sxx.get(k, 0.0) + (v - line) ** 2
    if not errs:
        return {}, {}, {}, 0
    pooled = sum(sum(e) for e in errs.values()) / sum(len(e) for e in errs.values())
    raw = {k: (len(e) / (sum(e) / len(e)) + PRIOR_GAMES / pooled) / (len(e) + PRIOR_GAMES) for k, e in errs.items()}
    mean_w = sum(raw.values()) / len(raw)
    weights = {k: round(v / mean_w, 4) for k, v in raw.items()}
    trust = {}
    for k, e in errs.items():
        n = len(e)
        slope = sxy[k] / sxx[k] if sxx[k] > 0 else 1.0
        trust[k] = round(min(1.2, max(0.0, (n * slope + PRIOR_GAMES * TRUST_PRIOR) / (n + PRIOR_GAMES))), 4)
    stats = {"mse": {k: round(sum(e) / len(e), 2) for k, e in errs.items()},
             "n": {k: len(e) for k, e in errs.items()},
             "slope": {k: round(sxy[k] / sxx[k], 3) if sxx[k] > 0 else None for k in errs}}
    return weights, trust, stats, len(games)


def learn(pred_path=None, weights_path=None):
    """Grade finished games and update each sport's source weights and trust factors.
    Saves data/consensus_weights.json and returns it."""
    pred_path, weights_path = pred_path or PRED_FILE, weights_path or WEIGHTS_FILE
    rows = []
    if os.path.exists(pred_path):
        with open(pred_path, newline="") as f:
            rows = list(csv.DictReader(f))
    finals = _finals({(r["sport"], r["game_date"]) for r in rows})
    out = load_weights(weights_path)
    for sport in sorted({r["sport"] for r in rows} | {sp for sp in SPORTS if os.path.exists(HISTORY_FILE.format(sport=sp))}):
        mg, tg = _history_games(sport)   # ESPN FPI's past predictions as a starting record
        for r in rows:
            if r["sport"] != sport or r["event_id"] not in finals:
                continue
            hp, ap = finals[r["event_id"]]
            if r["home_line"] not in ("", None):
                mg.append((json.loads(r["src_margins"]), hp - ap, -float(r["home_line"])))
            if r["total_line"] not in ("", None) and json.loads(r["src_totals"]):
                tg.append((json.loads(r["src_totals"]), hp + ap, float(r["total_line"])))
        mw, trust, mstat, mn = _fit([g for g in mg if g[0]])
        tw, trust_t, tstat, tn = _fit(tg)
        out[sport] = {"margin_w": mw, "trust_src": trust, "margin_stats": mstat, "games": mn,
                      "total_w": tw, "trust_total_src": trust_t, "total_stats": tstat, "total_games": tn,
                      "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")}
    os.makedirs(os.path.dirname(weights_path), exist_ok=True)
    with open(weights_path, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    return out


def _history_games(sport):
    """Past ESPN FPI predictions with closing lines and final scores (from backfill_fpi),
    as learning games. Only FPI publishes its old predictions, so only FPI gets a head start."""
    path = HISTORY_FILE.format(sport=sport)
    mg, tg = [], []
    if not os.path.exists(path):
        return mg, tg
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            act = float(r["home"]) - float(r["away"])
            mg.append(({"ESPN FPI": float(r["fpi_margin"])}, act, -float(r["home_line"])))
    return mg, tg


def backfill_fpi(sport, seasons=(2025, 2026)):
    """Rebuild data/history_fpi_<sport>.csv: for every finished regular-season game in
    `seasons`, ESPN FPI's pregame margin, DraftKings' closing spread and total, and the
    final score. Finished seasons are cached forever, so reruns are fast."""
    from concurrent.futures import ThreadPoolExecutor
    lg = SPORTS[sport]["espn"]
    sb = f"https://site.api.espn.com/apis/site/v2/sports/football/{lg}/scoreboard"
    this_year = datetime.now(timezone.utc).year
    games = []
    for year in seasons:
        for wk in range(1, 17 if sport == "cfb" else 19):
            p = {"dates": year, "week": wk, "seasontype": 2, "limit": 300, **({"groups": 80} if sport == "cfb" else {})}
            try:
                d, _ = cached_get(sb, params=p, ttl=None if year < this_year else 3600, source="ESPN scoreboard")
            except ApiError:
                continue
            games += [(year, wk, e) for e in d.get("events", []) if e["competitions"][0]["status"]["type"]["state"] == "post"]

    def one(item):
        year, wk, e = item
        c, eid = e["competitions"][0], e["id"]
        try:
            pr, _ = cached_get(f"https://sports.core.api.espn.com/v2/sports/football/leagues/{lg}/events/{eid}"
                               f"/competitions/{eid}/predictor", ttl=None, source="ESPN FPI")
            sm, _ = cached_get(f"https://site.api.espn.com/apis/site/v2/sports/football/{lg}/summary",
                               params={"event": eid}, ttl=None, source="ESPN summary")
        except ApiError:
            return None
        st = {x["name"]: x.get("value") for x in (pr.get("homeTeam") or {}).get("statistics", [])}
        pc = [x for x in sm.get("pickcenter", []) if x.get("spread") is not None]
        if st.get("teamPredPtDiff") is None or not pc:
            return None
        sc = {t["homeAway"]: float(t.get("score") or 0) for t in c["competitors"]}
        return {"year": year, "week": wk, "event_id": eid, "name": e["name"], "fpi_margin": st["teamPredPtDiff"],
                "home_line": pc[0]["spread"], "total_line": pc[0].get("overUnder"), "home": sc["home"], "away": sc["away"]}

    with ThreadPoolExecutor(8) as ex:
        rows = [r for r in ex.map(one, games) if r]
    path = HISTORY_FILE.format(sport=sport)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["year", "week", "event_id", "name", "fpi_margin", "home_line", "total_line",
                                          "home", "away"])
        w.writeheader()
        w.writerows(rows)
    return len(games), len(rows)
