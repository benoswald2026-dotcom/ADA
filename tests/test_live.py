import live


def _event(home_ball=True, poss_text="ATL 25", period=2, clock=95.0, hs=7, as_=14):
    return {"id": "1", "competitions": [{
        "status": {"clock": clock, "period": period, "type": {"state": "in", "shortDetail": "1:35 - 2nd"}},
        "competitors": [
            {"homeAway": "home", "score": str(hs), "team": {"id": "1", "abbreviation": "ATL", "displayName": "Atlanta Falcons"}},
            {"homeAway": "away", "score": str(as_), "team": {"id": "3", "abbreviation": "CHI", "displayName": "Chicago Bears"}}],
        "situation": {"possession": "1" if home_ball else "3", "possessionText": poss_text, "down": 2, "distance": 7,
                      "homeTimeouts": 3, "awayTimeouts": 2}}]}


def test_parse_state_home_perspective_and_field_position():
    s = live.parse_state(_event(home_ball=True, poss_text="ATL 25"))
    assert (s["home"], s["away"], s["home_score"], s["away_score"]) == ("ATL", "CHI", 7.0, 14.0)
    assert s["sec_left"] == 2 * 900 + 95 and s["home_ball"] is True
    assert s["yardline_100"] == 75                     # ATL ball at its own 25: 75 yards to go
    s = live.parse_state(_event(home_ball=False, poss_text="ATL 2"))
    assert s["home_ball"] is False and s["yardline_100"] == 2   # CHI at the ATL 2
    assert live.parse_state({"id": "2", "competitions": [{"status": {"type": {"state": "pre"}}}]}) is None


def test_win_prob_moves_the_right_way():
    w = {k: 0.0 for k in live.FEATURES}
    w.update(lead=0.1, spread=0.17, ball=-0.17, field=0.5)
    m = {"weights": w}
    base = {"home_score": 14, "away_score": 7, "sec_left": 1800, "spread": 0.0, "home_ball": True,
            "yardline_100": 75, "distance": 10, "home_to": 3, "away_to": 3}
    p = live.win_prob(m, base)
    assert 0.5 < p < 1
    assert live.win_prob(m, {**base, "home_score": 21}) > p          # bigger lead
    assert live.win_prob(m, {**base, "sec_left": 120}) > p           # same lead, less time
    assert live.win_prob(m, {**base, "yardline_100": 5}) > p         # ball near the goal line
    assert live.win_prob(m, {**base, "spread": None}) is None        # no pregame line: no number
