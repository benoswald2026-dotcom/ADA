"""Parlay pricing for 2- and 3-leg parlays."""
from odds import prob_to_american


def fair_parlay_prob(leg_probs):
    """Probability all legs win, ASSUMING the legs are independent:
    P(A and B) = P(A) * P(B).   0.6 * 0.7 = 0.42
    """
    if not 2 <= len(leg_probs) <= 3:
        raise ValueError("only 2-leg and 3-leg parlays are supported")
    p = 1.0
    for q in leg_probs:
        if not 0 < q < 1:
            raise ValueError("leg probabilities must be between 0 and 1")
        p *= q
    return p


def fair_price(leg_probs):
    """Returns (probability, fair American odds, fair decimal odds)."""
    p = fair_parlay_prob(leg_probs)
    return p, prob_to_american(p), 1 / p


def offered_decimal(leg_decimals):
    """A book prices a standard parlay by multiplying each leg's decimal odds."""
    d = 1.0
    for x in leg_decimals:
        d *= x
    return d


def expected_value(prob, decimal_odds):
    """Expected profit per $1 bet: p * payout - 1.  (0.42 * 2.6 - 1 = +0.092)"""
    return prob * decimal_odds - 1


def same_game_warning(game_ids):
    """Legs from the same game are correlated, so multiplying is wrong."""
    dupes = sorted({g for g in game_ids if list(game_ids).count(g) > 1}, key=str)
    if dupes:
        return (f"WARNING: multiple legs from the same game ({', '.join(map(str, dupes))}). "
                "These outcomes are correlated, so the 'independent' fair price below is NOT valid.")
    return None
