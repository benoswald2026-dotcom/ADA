"""Read-only API clients. Every request is a GET. Nothing here can place
a trade, bet, or order.

The CFBD API has used both snake_case (v1) and camelCase (v2) field names,
so each record is converted into one plain dict format right here. The rest
of the program only sees these normalized dicts.
"""
import config
from cache import cached_get

CFBD_BASE = "https://api.collegefootballdata.com"
ODDS_BASE = "https://api.the-odds-api.com/v4"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_CFB_SERIES = "KXNCAAFGAME"  # Kalshi series for college football game winners


def _pick(d, *names, default=None):
    """Return the first field name that exists in d (handles camelCase vs snake_case)."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- CFBD -----

def _cfbd(path, params, ttl):
    headers = {"Authorization": f"Bearer {config.get_key('CFBD_API_KEY')}",
               "Accept": "application/json"}
    return cached_get(CFBD_BASE + path, params=params, headers=headers,
                      ttl=ttl, source="CFBD")


def _ttl_for(year):
    from datetime import date
    return config.TTL_HISTORICAL if year < date.today().year else config.TTL_SCHEDULE


def normalize_game(g):
    home_cls = _pick(g, "homeClassification", "home_division", default="")
    away_cls = _pick(g, "awayClassification", "away_division", default="")
    return {
        "id": _pick(g, "id"),
        "season": _pick(g, "season"),
        "week": _pick(g, "week"),
        "season_type": _pick(g, "seasonType", "season_type"),
        "start": _pick(g, "startDate", "start_date"),
        "completed": bool(_pick(g, "completed", default=False)),
        "neutral": bool(_pick(g, "neutralSite", "neutral_site", default=False)),
        "home": _pick(g, "homeTeam", "home_team"),
        "away": _pick(g, "awayTeam", "away_team"),
        "home_pts": _pick(g, "homePoints", "home_points"),
        "away_pts": _pick(g, "awayPoints", "away_points"),
        "home_fbs": str(home_cls).lower() == "fbs",
        "away_fbs": str(away_cls).lower() == "fbs",
    }


def cfbd_games(year, week=None):
    """All games (regular + postseason) for a season. Returns (games, fetched_at)."""
    games, stamp = [], None
    for season_type in ("regular", "postseason"):
        params = {"year": year, "seasonType": season_type}
        if week is not None:
            if season_type == "postseason":
                continue
            params["week"] = week
        data, stamp = _cfbd("/games", params, _ttl_for(year))
        games += [normalize_game(g) for g in data]
    return games, stamp


def cfbd_advanced_game_stats(year):
    """Per-game, per-team offensive/defensive EPA ("PPA" in CFBD) and success rate."""
    data, stamp = _cfbd("/stats/game/advanced", {"year": year}, _ttl_for(year))
    rows = []
    for r in data:
        off, dfn = r.get("offense") or {}, r.get("defense") or {}
        rows.append({
            "game_id": _pick(r, "gameId", "game_id"),
            "week": _pick(r, "week"),
            "team": _pick(r, "team"),
            "opponent": _pick(r, "opponent"),
            "off_epa": _float(_pick(off, "ppa")),
            "def_epa": _float(_pick(dfn, "ppa")),
            "off_success": _float(_pick(off, "successRate", "success_rate")),
            "def_success": _float(_pick(dfn, "successRate", "success_rate")),
        })
    return rows, stamp


def cfbd_lines(year):
    """Closing lines (one list of providers per game). Spread is from the home
    team's view: -7 means the home team is favored by 7."""
    data, stamp = _cfbd("/lines", {"year": year}, _ttl_for(year))
    out = {}
    for g in data:
        lines = []
        for ln in g.get("lines") or []:
            lines.append({
                "provider": ln.get("provider"),
                "spread": _float(ln.get("spread")),
                "total": _float(_pick(ln, "overUnder", "over_under")),
                "home_ml": _float(_pick(ln, "homeMoneyline", "home_moneyline")),
                "away_ml": _float(_pick(ln, "awayMoneyline", "away_moneyline")),
            })
        out[_pick(g, "id")] = lines
    return out, stamp


def cfbd_calendar(year):
    data, stamp = _cfbd("/calendar", {"year": year}, config.TTL_SCHEDULE)
    weeks = [{
        "week": _pick(w, "week"),
        "season_type": _pick(w, "seasonType", "season_type"),
        "start": _pick(w, "startDate", "firstGameStart", "first_game_start"),
        "end": _pick(w, "endDate", "lastGameStart", "last_game_start"),
    } for w in data]
    return weeks, stamp


def cfbd_teams(year):
    """School + mascot for every team, used to match names across APIs."""
    data, stamp = _cfbd("/teams", {"year": year}, _ttl_for(year))
    return [{"school": _pick(t, "school"), "mascot": _pick(t, "mascot", default=""),
             "abbreviation": _pick(t, "abbreviation", default="")} for t in data], stamp


# ------------------------------------------------------------ Odds API -----

def odds_events():
    """Live NCAAF spreads, moneylines and totals from US books."""
    params = {"apiKey": config.get_key("ODDS_API_KEY"), "regions": "us",
              "markets": "h2h,spreads,totals", "oddsFormat": "american",
              "dateFormat": "iso"}
    return cached_get(ODDS_BASE + "/sports/americanfootball_ncaaf/odds",
                      params=params, ttl=config.TTL_ODDS, source="The Odds API")


# -------------------------------------------------------------- Kalshi -----

def _kalshi_price(m, field):
    """Kalshi gives prices in cents (yes_ask=56) or dollars (yes_ask_dollars='0.5600')."""
    dollars = _float(m.get(field + "_dollars"))
    if dollars is not None:
        return dollars
    cents = _float(m.get(field))
    return cents / 100 if cents is not None else None


def kalshi_markets(series=KALSHI_CFB_SERIES):
    """Open game-winner markets for one Kalshi series (public, no key).
    Returns (list of markets, fetched_at)."""
    markets, cursor, stamp = [], None, None
    for _page in range(20):  # safety cap
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data, stamp = cached_get(KALSHI_BASE + "/markets", params=params,
                                 ttl=config.TTL_KALSHI, source="Kalshi")
        for m in data.get("markets", []):
            markets.append({
                "ticker": m.get("ticker"),
                "event_ticker": m.get("event_ticker"),
                "title": m.get("title", ""),
                "team": m.get("yes_sub_title", ""),
                "yes_ask": _kalshi_price(m, "yes_ask"),
                "yes_bid": _kalshi_price(m, "yes_bid"),
                "last_price": _kalshi_price(m, "last_price"),
            })
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets, stamp
