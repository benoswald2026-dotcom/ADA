"""Step-by-step calculator for one game, built from the real model functions.

Used by the web app's "How it works" page so every number shown there comes
from the same code the `week` command uses. No API calls.
"""
from kalshi import edge_after_fee, kalshi_fee, should_flag
from odds import american_to_prob, remove_vig
from ratings import RatingSystem

EXAMPLE = {  # the README worked example (made-up inputs)
    "home": "Home U", "away": "Away State", "neutral": False,
    "elo_home": 1650, "elo_away": 1580,
    "home_off": 0.10, "home_def": -0.05, "away_off": 0.06, "away_def": 0.01,
    "games": 6, "injury_home": 0.0, "injury_away": 0.0,
    "books": [[-200, 170], [-210, 175]],
    "kalshi_home": 0.64, "kalshi_away": 0.38,
}


def explain(inp):
    x = {**EXAMPLE, **(inp or {})}
    home, away = x["home"] or "Home", x["away"] or "Away"
    if home == away:
        away += " (away)"

    rs = RatingSystem()
    rs.elo = {home: float(x["elo_home"]), away: float(x["elo_away"])}
    games = int(x["games"])
    if games > 0:
        rs._epa_cache = {home: (float(x["home_off"]), float(x["home_def"]), games),
                         away: (float(x["away_off"]), float(x["away_def"]), games)}
    else:
        rs._epa_cache = {}
    injury_adj = float(x["injury_away"]) - float(x["injury_home"])
    pred = rs.predict(home, away, bool(x["neutral"]), injury_adj=injury_adj)

    books = []
    for h, a in x["books"]:
        ih, ia = american_to_prob(h), american_to_prob(a)
        nh, na = remove_vig(h, a)
        books.append({"home_ml": h, "away_ml": a, "implied_home": ih, "implied_away": ia,
                      "overround": ih + ia, "fair_home": nh, "fair_away": na})
    consensus = sum(b["fair_home"] for b in books) / len(books) if books else None

    p_home = pred["home_prob"]
    sides = []
    for side, team, p, price in (("home", home, p_home, x["kalshi_home"]),
                                 ("away", away, 1 - p_home, x["kalshi_away"])):
        if price is None or not 0 < float(price) < 1:
            continue
        price = float(price)
        m = None if consensus is None else (consensus if side == "home" else 1 - consensus)
        e = edge_after_fee(p, price)
        sides.append({"side": side, "team": team, "model": p, "market": m, "price": price,
                      "fee": kalshi_fee(price), "edge": e, "flag": should_flag(e, p, m)})
    best = max(sides, key=lambda s: s["edge"]) if sides else None

    return {"inputs": x, "home": home, "away": away, "pred": {
                k: pred[k] for k in ("elo_home", "elo_away", "hfa", "elo_margin", "epa_margin",
                                     "epa_weight", "injury_adj", "margin", "home_spread", "home_prob")},
            "books": books, "consensus": consensus, "kalshi": sides, "best": best}
