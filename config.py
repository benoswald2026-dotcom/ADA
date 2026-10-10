"""Settings and API-key loading.

Every model constant lives here so it is easy to see that nothing is tuned
on the test seasons. These values were picked BEFORE running any backtest,
using common published Elo conventions (FiveThirtyEight-style), and have
not been changed based on 2023-2025 results.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # reads a local .env file if one exists

# ---- Elo rating -----------------------------------------------------------
ELO_START_FBS = 1500.0      # new FBS team
ELO_START_OTHER = 1200.0    # new FCS / lower-division team (much weaker on average)
ELO_K = 25.0                # how far one game moves a rating
ELO_PER_POINT = 25.0        # 25 Elo points ~= 1 point of spread
SEASON_REGRESSION = 1 / 3   # each offseason, move 1/3 of the way back to the mean
HOME_FIELD_POINTS = 2.5     # home-field advantage in points (0 for neutral sites)

# ---- EPA adjustment -------------------------------------------------------
PLAYS_PER_GAME = 65         # converts EPA/play into points per game
EPA_WEIGHT_MAX = 0.4        # max share of the spread that comes from EPA
EPA_FULL_WEIGHT_GAMES = 6   # EPA reaches full weight after this many games
EPA_OPP_ADJ_ITERATIONS = 5  # passes of the opponent adjustment

# ---- Total (over/under) ---------------------------------------------------
TOTAL_SHRINK_GAMES = 4      # shrink team scoring toward league average early on

# ---- Betting rules (from the project spec) -------------------------------
KALSHI_FEE_RATE = 0.07      # fee per contract = 0.07 * P * (1 - P)
MIN_EDGE = 0.03             # flag only if edge after fees >= 3 percentage points
MAX_MODEL_MARKET_GAP = 0.10 # ...and model vs market consensus within 10 points

# ---- Backtest -------------------------------------------------------------
BURN_IN_SEASONS = [2021, 2022]  # ratings warm up here; never scored
TEST_SEASONS = [2023, 2024, 2025]

# ---- Cache lifetimes (seconds) -------------------------------------------
TTL_HISTORICAL = None       # completed seasons never change -> keep forever
TTL_SCHEDULE = 6 * 3600
TTL_ODDS = 30 * 60          # Odds API free tier is ~500 credits/month
TTL_KALSHI = 5 * 60

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


class MissingKeyError(RuntimeError):
    pass


def get_key(name):
    """Return an API key from the environment. Never print the value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingKeyError(
            f"{name} is not set. Add it to a .env file (see .env.example) "
            f"or export it in your shell."
        )
    return value
