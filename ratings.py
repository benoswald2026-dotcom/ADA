"""Team power ratings: Elo + opponent-adjusted EPA/play + home field.

Turns two teams into a predicted spread, win probability, and total.
Everything here uses only games that have already been fed in through
update(), so if the caller feeds games in date order there is no lookahead.
"""
import math
from collections import defaultdict

import config


# ---------------------------------------------------------- pure Elo math --

def expected_score(rating_a, rating_b):
    """Elo win probability for A vs B. 100-point edge -> 0.640, 0 -> 0.500."""
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def mov_multiplier(margin, winner_elo_diff):
    """Margin-of-victory multiplier (FiveThirtyEight style).

    ln(|margin| + 1) grows slowly, so a 35-point win counts more than a
    3-point win but not 12x more. The second factor shrinks the bonus when
    a big favorite wins big (that was expected), which stops ratings from
    inflating.
    """
    return math.log(abs(margin) + 1) * 2.2 / (winner_elo_diff * 0.001 + 2.2)


def elo_update(r_home, r_away, home_pts, away_pts, home_field_elo=0.0, k=config.ELO_K):
    """Return new (home, away) ratings after a game.

    change = K * MOV multiplier * (actual result - expected result)
    The home team gets home_field_elo extra points when computing the
    expected result only. Points gained by one team are lost by the other.
    """
    exp_home = expected_score(r_home + home_field_elo, r_away)
    if home_pts > away_pts:
        actual, diff = 1.0, (r_home + home_field_elo) - r_away
    elif home_pts < away_pts:
        actual, diff = 0.0, r_away - (r_home + home_field_elo)
    else:
        actual, diff = 0.5, 0.0
    mult = mov_multiplier(home_pts - away_pts, diff) if actual != 0.5 else 1.0
    change = k * mult * (actual - exp_home)
    return r_home + change, r_away - change


def margin_to_prob(margin, elo_per_point=config.ELO_PER_POINT):
    """Predicted home margin (points) -> home win probability, using the
    same Elo curve: margin * 25 Elo/point, then expected_score."""
    return 1 / (1 + 10 ** (-margin * elo_per_point / 400))


def opponent_adjusted_epa(rows, iterations=config.EPA_OPP_ADJ_ITERATIONS):
    """rows: list of dicts with team, opponent, off_epa, def_epa (one per team-game).

    Returns {team: (adj_off, adj_def, games)} where
      adj_off = EPA/play the offense gains ABOVE league average (higher = better)
      adj_def = EPA/play the defense ALLOWS above league average (lower = better)
    after removing the strength of the opponents faced.

    How: start from raw averages, then repeat: a team's offense number is
    its game EPA minus what that opponent's defense normally allows.
    """
    rows = [r for r in rows if r["off_epa"] is not None and r["def_epa"] is not None]
    if not rows:
        return {}
    mu = sum(r["off_epa"] for r in rows) / len(rows)
    games = defaultdict(list)
    for r in rows:
        games[r["team"]].append(r)
    off = {t: sum(g["off_epa"] for g in gs) / len(gs) - mu for t, gs in games.items()}
    dfn = {t: sum(g["def_epa"] for g in gs) / len(gs) - mu for t, gs in games.items()}
    for _ in range(iterations):
        new_off = {t: sum(g["off_epa"] - mu - dfn.get(g["opponent"], 0.0) for g in gs) / len(gs)
                   for t, gs in games.items()}
        new_def = {t: sum(g["def_epa"] - mu - off.get(g["opponent"], 0.0) for g in gs) / len(gs)
                   for t, gs in games.items()}
        off, dfn = new_off, new_def
    return {t: (off[t], dfn[t], len(games[t])) for t in games}


# ---------------------------------------------------------- rating system --

class RatingSystem:
    def __init__(self, cfg=config.CFB):
        self.cfg = cfg                            # sport constants (config.CFB or config.NFL)
        self.elo = {}
        self.is_fbs = {}                          # team -> was FBS when last seen
        self.season_epa_rows = []                 # this season only
        self.points = defaultdict(list)           # team -> [(for, against)] this season
        self.league_avg_pts = 28.0                # per team per game; updated from data
        self._epa_cache = None

    # --- season handling
    def start_season(self):
        """Seed from the prior season: pull every rating 1/3 back toward the mean."""
        for team, r in self.elo.items():
            mean = self.cfg.ELO_START_FBS if self.is_fbs.get(team, True) else self.cfg.ELO_START_OTHER
            self.elo[team] = r + (mean - r) * self.cfg.SEASON_REGRESSION
        all_pts = [p for lst in self.points.values() for p, _ in lst]
        if all_pts:
            self.league_avg_pts = sum(all_pts) / len(all_pts)
        self.season_epa_rows = []
        self.points = defaultdict(list)
        self._epa_cache = None

    def rating(self, team, is_fbs=True):
        self.is_fbs[team] = is_fbs
        if team not in self.elo:
            self.elo[team] = self.cfg.ELO_START_FBS if is_fbs else self.cfg.ELO_START_OTHER
        return self.elo[team]

    def epa_table(self):
        if self._epa_cache is None:
            self._epa_cache = opponent_adjusted_epa(self.season_epa_rows)
        return self._epa_cache

    # --- prediction
    def predict(self, home, away, neutral=False, home_fbs=True, away_fbs=True, injury_adj=0.0):
        """Predict a game using only what update() has seen so far.

        Returns a dict. 'margin' = predicted home points minus away points.
        'home_spread' uses betting convention: negative = home favored.
        """
        hfa = 0.0 if neutral else self.cfg.HOME_FIELD_POINTS
        r_h, r_a = self.rating(home, home_fbs), self.rating(away, away_fbs)
        elo_margin = (r_h - r_a) / self.cfg.ELO_PER_POINT + hfa

        epa = self.epa_table()
        epa_margin, weight = None, 0.0
        if home in epa and away in epa:
            off_h, def_h, n_h = epa[home]
            off_a, def_a, n_a = epa[away]
            epa_margin = self.cfg.PLAYS_PER_GAME * ((off_h + def_a) - (off_a + def_h)) + hfa
            weight = self.cfg.EPA_WEIGHT_MAX * min(min(n_h, n_a) / self.cfg.EPA_FULL_WEIGHT_GAMES, 1.0)

        base = elo_margin if epa_margin is None else (1 - weight) * elo_margin + weight * epa_margin
        margin = base + injury_adj
        return {
            "elo_home": r_h, "elo_away": r_a, "hfa": hfa,
            "elo_margin": elo_margin, "epa_margin": epa_margin, "epa_weight": weight,
            "injury_adj": injury_adj, "margin": margin, "home_spread": -margin,
            "home_prob": margin_to_prob(margin, self.cfg.ELO_PER_POINT), "total": self.predict_total(home, away),
        }

    def predict_total(self, home, away):
        """Each team's expected points = league avg + (its scoring vs avg)
        + (opponent's points allowed vs avg), each shrunk toward average
        when a team has played few games. Total = sum of both."""
        avg, k = self.league_avg_pts, self.cfg.TOTAL_SHRINK_GAMES

        def tilt(team):
            g = self.points.get(team, [])
            if not g:
                return 0.0, 0.0
            s = len(g) / (len(g) + k)
            pf = sum(p for p, _ in g) / len(g)
            pa = sum(a for _, a in g) / len(g)
            return s * (pf - avg), s * (pa - avg)

        off_h, def_h = tilt(home)
        off_a, def_a = tilt(away)
        return (avg + off_h + def_a) + (avg + off_a + def_h)

    # --- learning
    def update(self, game, epa_rows=()):
        """Feed in one completed game (normalized CFBD dict) and its EPA rows."""
        if game["home_pts"] is None or game["away_pts"] is None:
            return
        hfa_elo = 0.0 if game["neutral"] else self.cfg.HOME_FIELD_POINTS * self.cfg.ELO_PER_POINT
        h, a = game["home"], game["away"]
        new_h, new_a = elo_update(self.rating(h, game["home_fbs"]), self.rating(a, game["away_fbs"]),
                                  game["home_pts"], game["away_pts"], hfa_elo, k=self.cfg.ELO_K)
        self.elo[h], self.elo[a] = new_h, new_a
        self.points[h].append((game["home_pts"], game["away_pts"]))
        self.points[a].append((game["away_pts"], game["home_pts"]))
        self.season_epa_rows.extend(epa_rows)
        self._epa_cache = None
