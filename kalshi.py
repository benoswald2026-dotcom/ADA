"""Kalshi fee and edge math, plus matching Kalshi markets to games. Read-only."""
import config
from names import team_key


def kalshi_fee(price):
    """Kalshi trading fee per contract, in dollars: 0.07 * P * (1 - P).
    At P = 0.50 the fee is 0.07 * 0.5 * 0.5 = $0.0175 (1.75 cents).
    (Kalshi rounds the total fee on an order UP to the next cent; for a
    single contract this formula is the un-rounded value.)
    """
    if not 0 <= price <= 1:
        raise ValueError("price must be between 0 and 1 dollars")
    return config.KALSHI_FEE_RATE * price * (1 - price)


def edge_after_fee(model_prob, price):
    """Expected profit per $1 contract if you buy YES at `price`.

    The contract pays $1 if the team wins. Expected value = model_prob * $1.
    Cost = price + fee.   Edge = model_prob - price - fee.
    Example: model 0.60, price 0.55 -> fee 0.0173 -> edge 0.0327 (3.3 points).
    """
    return model_prob - price - kalshi_fee(price)


def should_flag(edge, model_prob, market_prob, price=None):
    """FLAG only if edge >= 3 points AND model and market are within 10 points.
    Cheap YES contracts (under strategy.LONGSHOT_MIN_PRICE) are never flagged:
    on Kalshi they lose far more than their price implies (burgi-deng-whelan-2025)."""
    from strategy import LONGSHOT_MIN_PRICE
    if edge is None or market_prob is None:
        return False
    if price is not None and price < LONGSHOT_MIN_PRICE:
        return False
    return edge >= config.MIN_EDGE and abs(model_prob - market_prob) <= config.MAX_MODEL_MARKET_GAP


def match_market(markets, home, away):
    """Find the Kalshi event that has a YES market for BOTH teams.
    Returns {"home": market, "away": market} or None ("no market").
    Matching is by exact normalized team name plus a fixed alias table
    (names.team_key); no fuzzy guessing.
    """
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], []).append(m)
    h, a = team_key(home), team_key(away)
    found = []
    for event_markets in by_event.values():
        named = {team_key(m["team"]): m for m in event_markets}
        if h in named and a in named:
            found.append({"home": named[h], "away": named[a]})
    return found[0] if len(found) == 1 else None
