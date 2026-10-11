"""Sportsbook odds math: American odds -> probability, vig removal, consensus."""
from statistics import median


def american_to_prob(odds):
    """Implied probability INCLUDING the book's vig.
    -150 -> 150/250 = 0.600     +130 -> 100/230 = 0.435
    """
    odds = float(odds)
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def american_to_decimal(odds):
    """Decimal payout per $1 staked (stake included). -150 -> 1.667, +130 -> 2.30"""
    odds = float(odds)
    return 1 + (100 / -odds if odds < 0 else odds / 100)


def prob_to_american(p):
    """Fair American odds for probability p. 0.6 -> -150, 0.4 -> +150"""
    if not 0 < p < 1:
        raise ValueError("probability must be between 0 and 1")
    if p >= 0.5:
        return -100 * p / (1 - p)
    return 100 * (1 - p) / p


def remove_vig(odds_a, odds_b):
    """Two-way market -> fair probabilities that sum to 1.

    The two implied probabilities add up to more than 1 (e.g. 0.600 + 0.435
    = 1.035); the extra 3.5% is the vig. Divide each by the total so they
    sum to exactly 1 ("proportional" or "multiplicative" method).
    """
    pa, pb = american_to_prob(odds_a), american_to_prob(odds_b)
    total = pa + pb
    return pa / total, pb / total


def consensus(book_prices):
    """book_prices: list of (home_odds, away_odds), one per book.
    Returns average no-vig home win probability across books (or None)."""
    probs = [remove_vig(h, a)[0] for h, a in book_prices if h is not None and a is not None]
    if not probs:
        return None
    return sum(probs) / len(probs)


def parse_odds_event(event, home_name, away_name):
    """Pull one Odds API event apart into per-book numbers.

    Returns dict with: books (count), home_prob (no-vig consensus),
    home_spread (median across books, home view), total (median),
    home_ml_dec / away_ml_dec (average decimal moneyline, for parlays),
    updated (latest book update time).
    """
    ml_pairs, spreads, totals, updates = [], [], [], []
    spread_prices = {"home": [], "away": []}
    for book in event.get("bookmakers", []):
        updates.append(book.get("last_update", ""))
        for market in book.get("markets", []):
            outs = {o["name"]: o for o in market.get("outcomes", [])}
            if market["key"] == "h2h" and home_name in outs and away_name in outs:
                ml_pairs.append((outs[home_name]["price"], outs[away_name]["price"]))
            elif market["key"] == "spreads" and home_name in outs:
                if outs[home_name].get("point") is not None:
                    spreads.append(float(outs[home_name]["point"]))
                    spread_prices["home"].append(american_to_decimal(outs[home_name]["price"]))
                    if away_name in outs:
                        spread_prices["away"].append(american_to_decimal(outs[away_name]["price"]))
            elif market["key"] == "totals" and "Over" in outs:
                if outs["Over"].get("point") is not None:
                    totals.append(float(outs["Over"]["point"]))
    return {
        "books": len(event.get("bookmakers", [])),
        "ml_books": len(ml_pairs),
        "home_prob": consensus(ml_pairs),
        "home_spread": median(spreads) if spreads else None,
        "total": median(totals) if totals else None,
        "home_ml_dec": (sum(american_to_decimal(h) for h, _ in ml_pairs) / len(ml_pairs)) if ml_pairs else None,
        "away_ml_dec": (sum(american_to_decimal(a) for _, a in ml_pairs) / len(ml_pairs)) if ml_pairs else None,
        "updated": max(updates) if updates else None,
        "home_spread_dec": (sum(spread_prices["home"]) / len(spread_prices["home"])) if spread_prices["home"] else None,
        "away_spread_dec": (sum(spread_prices["away"]) / len(spread_prices["away"])) if spread_prices["away"] else None,
    }
