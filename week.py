"""Builds the current-week comparison: model vs market consensus vs Kalshi."""
from datetime import datetime, timedelta, timezone

import config
import injuries as inj
from api_clients import (cfbd_advanced_game_stats, cfbd_calendar, cfbd_games,
                         cfbd_teams, kalshi_markets, odds_events)
from backtest import replay_season
from kalshi import edge_after_fee, match_market, should_flag
from names import normalize, odds_name_to_school
from odds import parse_odds_event
from ratings import RatingSystem


def current_season(now):
    """Jan/Feb games belong to the previous season (bowls, playoff)."""
    return now.year - 1 if now.month <= 2 else now.year


def _parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def find_current_week(calendar, now):
    """First calendar week whose end hasn't passed yet (12h grace for late games)."""
    for w in sorted(calendar, key=lambda w: w["start"] or ""):
        end = _parse_time(w["end"])
        if end and now <= end + timedelta(hours=12):
            return w
    return None


def _build_ratings(year):
    """Replay every season from the burn-in start through last season, then
    this season's completed games. Returns (rating system, CFBD timestamp)."""
    rs = RatingSystem()
    for y in range(config.BURN_IN_SEASONS[0], year):
        replay_season(rs, y)
    # Current season: learn only from completed games.
    rs.start_season()
    games, stamp = cfbd_games(year)
    adv, adv_stamp = cfbd_advanced_game_stats(year)
    by_game = {}
    for r in adv:
        by_game.setdefault(r["game_id"], []).append(r)
    for g in sorted([g for g in games if g["completed"]], key=lambda g: g["start"] or ""):
        rs.update(g, by_game.get(g["id"], []))
    return rs, min(stamp, adv_stamp)


def _match_odds(events, teams, home, away):
    """Find the Odds API event for this game. Returns (event, odds_home_name, odds_away_name)."""
    for e in events:
        h = odds_name_to_school(e["home_team"], teams)
        a = odds_name_to_school(e["away_team"], teams)
        if (h, a) == (home, away):
            return e, e["home_team"], e["away_team"]
        if (h, a) == (away, home):  # neutral site listed the other way round
            return e, e["away_team"], e["home_team"]
    return None, None, None


def build_week(now=None):
    """Returns (week info, list of row dicts)."""
    now = now or datetime.now(timezone.utc)
    year = current_season(now)
    calendar, _ = cfbd_calendar(year)
    week = find_current_week(calendar, now)
    if week is None:
        raise RuntimeError(f"No upcoming week found in the {year} CFBD calendar.")

    rs, model_stamp = _build_ratings(year)
    games, _ = cfbd_games(year)  # cached; same call _build_ratings made
    games = [g for g in games if g["week"] == week["week"]
             and g["season_type"] == week["season_type"] and not g["completed"]
             and (g["home_fbs"] or g["away_fbs"])]

    teams, _ = cfbd_teams(year)
    events, odds_stamp = odds_events()
    markets, kalshi_stamp = kalshi_markets()
    injury_pts = inj.load_injuries()

    rows = []
    for g in sorted(games, key=lambda g: g["start"] or ""):
        adj = inj.spread_adjustment(injury_pts, g["home"], g["away"])
        pred = rs.predict(g["home"], g["away"], g["neutral"], g["home_fbs"], g["away_fbs"], adj)

        event, oh, oa = _match_odds(events, teams, g["home"], g["away"])
        mkt = parse_odds_event(event, oh, oa) if event else None

        km = match_market(markets, g["home"], g["away"])
        p_home = pred["home_prob"]
        mkt_home = mkt["home_prob"] if mkt else None

        best = None  # (edge, team, side model prob, side market prob, price)
        if km:
            for side, team, p in (("home", g["home"], p_home), ("away", g["away"], 1 - p_home)):
                price = km[side]["yes_ask"]
                if price is None or not 0 < price < 1:
                    continue  # no one selling YES right now
                m_side = None if mkt_home is None else (mkt_home if side == "home" else 1 - mkt_home)
                e = edge_after_fee(p, price)
                if best is None or e > best[0]:
                    best = (e, team, p, m_side, price)

        flag = bool(best) and should_flag(best[0], best[2], best[3], price=best[4])
        rows.append({
            "game": g, "pred": pred, "market": mkt, "kalshi": km, "best": best, "flag": flag,
            "sources": {
                "model": f"CFBD games+adv. stats @ {model_stamp}",
                "market": (f"The Odds API, {mkt['ml_books']} book{'s' if mkt['ml_books'] != 1 else ''}, book update {mkt['updated']}"
                           if mkt else f"The Odds API @ {odds_stamp}: no odds for this game"),
                "kalshi": (f"Kalshi {km['home']['event_ticker']} @ {kalshi_stamp}"
                           if km else f"Kalshi @ {kalshi_stamp}: no market"),
            },
        })
    return week, rows


def find_row(rows, game_id):
    for r in rows:
        if str(r["game"]["id"]) == str(game_id):
            return r
    return None


def side_of(row, team):
    """'home' or 'away' for a team name typed by the user, else None."""
    t = normalize(team)
    g = row["game"]
    for side in ("home", "away"):
        if t == normalize(g[side]) or t == normalize(g.get(f"{side}_code") or "\0"):
            return side
    if g.get("home_code"):  # NFL: also accept "Packers", "Green Bay", ...
        import nfl_teams
        code = nfl_teams.code_for(team)
        for side in ("home", "away"):
            if code and code == g[f"{side}_code"]:
                return side
    return None


class ParlayInputError(ValueError):
    pass


def price_parlay(rows, picks, offered=None):
    """picks: list of (game_id, team name). offered: optional American odds.
    Returns a dict with the legs, fair prices, offered price, and EV."""
    from odds import american_to_decimal, prob_to_american
    from parlay import expected_value, fair_price, offered_decimal, same_game_warning

    if not 2 <= len(picks) <= 3:
        raise ParlayInputError("Pick 2 or 3 legs.")
    legs = []
    for gid, team in picks:
        row = find_row(rows, gid)
        if row is None:
            raise ParlayInputError(f"Game {gid} is not in this week's games.")
        side = side_of(row, team)
        if side is None:
            g = row["game"]
            raise ParlayInputError(f"'{team}' is not in game {gid} ({g['away']} @ {g['home']}).")
        p_model = row["pred"]["home_prob"] if side == "home" else 1 - row["pred"]["home_prob"]
        m = row["market"]
        p_mkt = None if not m or m["home_prob"] is None else (
            m["home_prob"] if side == "home" else 1 - m["home_prob"])
        legs.append({"game_id": row["game"]["id"], "team": row["game"][side],
                     "matchup": f"{row['game']['away']} @ {row['game']['home']}",
                     "p_model": p_model, "p_market": p_mkt,
                     "decimal": None if not m else m[f"{side}_ml_dec"]})

    p, american, dec = fair_price([l["p_model"] for l in legs])
    result = {"legs": legs, "warning": same_game_warning([l["game_id"] for l in legs]),
              "model": {"prob": p, "american": american, "decimal": dec}, "market": None,
              "offered": None}
    if all(l["p_market"] for l in legs):
        pm, am, dm = fair_price([l["p_market"] for l in legs])
        result["market"] = {"prob": pm, "american": am, "decimal": dm}

    if offered is not None:
        off_dec, how = american_to_decimal(offered), "entered"
    elif all(l["decimal"] for l in legs):
        off_dec, how = offered_decimal([l["decimal"] for l in legs]), "book moneylines multiplied"
    else:
        off_dec = None
    if off_dec:
        result["offered"] = {"decimal": off_dec, "american": prob_to_american(1 / off_dec),
                             "implied": 1 / off_dec, "how": how,
                             "ev": expected_value(p, off_dec)}
    return result
