import pytest

import consensus as C


def test_tier_rules():
    assert C.tier_for(3.5, 3, 3, 3.0, 1.5) == "Strong"
    assert C.tier_for(3.5, 3, 2, 3.0, 1.5) == "Lean"       # not unanimous
    assert C.tier_for(1.0, 3, 3, 3.0, 1.5) is None          # line close to consensus
    assert C.tier_for(2.0, 2, 1, 3.0, 1.5) is None          # sources split
    assert C.tier_for(-4.0, 3, 3, 3.0, 1.5) == "Strong"     # direction doesn't matter


def test_grade_spread_and_total():
    p = {"bet_type": "Spread", "pick": "Indiana +3", "home": "Indiana", "line": "3.0"}
    assert C.grade_pick(p, 24, 26) == "W"      # Indiana lost by 2, +3 covers
    assert C.grade_pick(p, 24, 27) == "P"
    assert C.grade_pick(p, 20, 27) == "L"
    away = {"bet_type": "Spread", "pick": "Ohio State -3", "home": "Indiana", "line": "3.0"}
    assert C.grade_pick(away, 20, 27) == "W"   # Ohio State won by 7
    over = {"bet_type": "Total", "pick": "Over 50.5", "home": "Indiana", "line": "50.5"}
    assert C.grade_pick(over, 30, 21) == "W" and C.grade_pick(over, 20, 21) == "L"


def test_parse_dratings_row():
    html = ('<tr><td><time datetime="2026-10-17T16:00:00Z"></time></td><td>'
            '<a href="/teams/x/1-ohio-state-buckeyes">Ohio State Buckeyes</a><br />'
            '<a href="/teams/x/2-indiana-hoosiers">Indiana Hoosiers</a></td>'
            '<td class="table-division"><span class="tc--green">56.8%</span><br /><span class="tc--red">43.2%</span></td>'
            '<td class="table-division">26.8<br />24.1</td><td>50.9</td></tr>')
    g = C.parse_dratings(html)[0]
    assert g["away"] == "Ohio State Buckeyes" and g["home"] == "Indiana Hoosiers"
    assert g["p_home"] == pytest.approx(0.432) and g["total"] == pytest.approx(50.9)
    assert g["s_home"] - g["s_away"] == pytest.approx(-2.7)


def test_parse_sagarin_lines():
    text = ("HOME ADVANTAGE=[  4.04]      [  4.01]       [  4.06]\n"
            "   3  Ohio State           A  =  94.69    5   1   68.51(  45)    0   1  |    1   1  |   93.87    2 |   96.35    3 |\n"
            "  12  Miami-Florida        A  =  88.10    5   1   70.00(  20)    0   1  |    1   1  |   87.50   12 |   88.00   10 |\n")
    ratings, hfa = C.parse_sagarin(text.replace("\\\n", "\n"))
    assert hfa == pytest.approx(4.01)
    assert ratings[C.team_key("Ohio State")] == pytest.approx(93.87)
    assert ratings[C.team_key("Miami")] == pytest.approx(87.50)      # alias: Miami-Florida = Miami


def test_log_picks_replaces_same_day_and_skips_started(tmp_path):
    from datetime import datetime, timedelta, timezone
    soon = datetime.now(timezone.utc) + timedelta(hours=3)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    def result(tier, kick):
        gm = {"event_id": "1", "kickoff": kick, "matchup": "A @ B", "home": "B", "away": "A", "margin": -1.0, "total": 50,
              "ats": {"line": 3.0, "gap": 2.0, "pick": "B +3", "agree": 3, "n": 3, "tier": tier}, "ou": None}
        return {"sport": "cfb", "games": [gm]}
    path = str(tmp_path / "p.csv")
    assert C.log_picks(result("Strong", soon), path) == 1
    assert C.log_picks(result("Lean", soon), path) == 0         # same day: replaced, not added
    import csv
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 1 and rows[0]["tier"] == "Lean"
    assert C.log_picks({"sport": "cfb", "games": [{**result("Strong", past)["games"][0], "event_id": "2"}]}, path) == 0


def test_strong_with_many_sources_needs_80_percent():
    assert C.tier_for(3.5, 5, 4, 3.0, 1.5) == "Strong"
    assert C.tier_for(3.5, 6, 4, 3.0, 1.5) == "Lean"       # 4/6 < 80%
    assert C.tier_for(3.5, 4, 3, 3.0, 1.5) == "Lean"       # under 5 sources: all must agree


def test_parse_dimers_and_oddsshark():
    dim = ('"AwayTeam":{"Abv":"OSU","DisplayName":"Ohio State"},"x":1,"HomeTeam":{"Abv":"IND","DisplayName":"Indiana"},'
           '"y":2,"PreData":{"PredAwayScore":27.06,"PredHomeScore":24.01,"PythagHome":0.4018}')
    g = C.parse_dimers(dim + dim)[0]                       # duplicates collapse
    assert g["home"] == "Indiana" and g["p_home"] == pytest.approx(0.4018) and len(C.parse_dimers(dim + dim)) == 1
    osk = ("class=\"game-prediction-card-matchup\"><a href='/sport/football/ncaaf/teams/main/cincinnati-bearcats' "
           "class='team-anchor team-anchor-away'><img alt=\"CIN logo\"></a><span>x</span> 30.71 </span>"
           "<a href='/sport/football/ncaaf/teams/main/west-virginia-mountaineers' class='team-anchor team-anchor-home'>"
           "<img alt=\"WVU logo\"></a><span>y</span> 32.43 </span>")
    o = C.parse_oddsshark("x" + osk)[0]
    assert o["away"] == "cincinnati bearcats" and o["s_home"] == pytest.approx(32.43)


def test_name_matching_handles_mascots_entities_and_long_names():
    hk = {C.team_key("UL Monroe"), "ul monroe warhawks"}
    ak = {C.team_key("Louisiana Tech"), "louisiana tech bulldogs"}
    rows = [{"home": "ULM Warhawks", "away": "Louisiana Tech Bulldogs"}]
    assert C._find(rows, hk, ak) is rows[0]
    assert C.team_key("Southern Methodist") == C.team_key("SMU")
    assert C.team_key("Texas-El Paso") == C.team_key("UTEP")
    assert C.team_key("Army West Point") == C.team_key("Army")
    d = C.parse_dratings('<tr><a href="/teams/x/1">Troy Trojans</a><a href="/teams/x/2">Louisiana Ragin&#39; Cajuns</a>'
                         '<span>40.0%</span><span>60.0%</span></tr>')[0]
    assert d["home"] == "Louisiana Ragin' Cajuns"
    # both teams must match: a lone mascot-stripped hit never pairs the wrong game
    assert C._find([{"home": "Miami RedHawks", "away": "Akron Zips"}], {C.team_key("Miami")}, {C.team_key("Toledo")}) is None


def test_fit_weights_and_per_source_trust():
    import random
    rnd = random.Random(4)
    games = []
    for _ in range(400):
        line = rnd.uniform(-14, 14)
        actual = line + rnd.gauss(0, 14)
        sharp = actual + rnd.gauss(0, 8)            # its gaps carry real information
        noise = line + rnd.gauss(0, 6)               # its gaps are pure noise
        games.append(({"Sharp": sharp, "Noise": noise}, actual, line))
    w, trust, stats, n = C._fit(games)
    assert w["Sharp"] > w["Noise"] and n == 400
    assert trust["Sharp"] > 0.6 and trust["Noise"] < 0.25


def test_few_games_keep_defaults_close():
    w, trust, _, n = C._fit([({"A": 3.0, "B": -3.0}, 10.0, 0.0)])
    assert n == 1 and 0.9 < w["A"] / w["B"] < 1.1 and all(0.85 <= t <= 1.2 for t in trust.values())


def test_untrusted_source_neither_moves_gap_nor_votes():
    g = {"id": "1", "kickoff": None, "home": {"location": "B"}, "away": {"location": "A"},
         "home_spread": (-3.0, -110), "away_spread": (3.0, -110), "over": None, "under": None,
         "home_ml": None, "away_ml": None}
    src = {"FPI": {"p": 0.8, "margin": 12.0}, "X": {"p": 0.6, "margin": 4.0}, "Y": {"p": 0.6, "margin": 4.5}}
    learned = {"trust_src": {"FPI": 0.0, "X": 1.0, "Y": 1.0}}
    out = C._summarize("cfb", g, {}, src, C.TIERS["cfb"], None, learned)
    assert out["ats"]["n"] == 2                               # FPI no longer votes
    assert out["ats"]["gap"] < out["ats"]["gap_raw"]          # and its big gap is ignored
