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

class _Sport:
    """Read-only bundle of one sport's model constants."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


# College football: the constants above.
CFB = _Sport(
    NAME="cfb", ELO_START_FBS=ELO_START_FBS, ELO_START_OTHER=ELO_START_OTHER, ELO_K=ELO_K,
    ELO_PER_POINT=ELO_PER_POINT, SEASON_REGRESSION=SEASON_REGRESSION,
    HOME_FIELD_POINTS=HOME_FIELD_POINTS, PLAYS_PER_GAME=PLAYS_PER_GAME,
    EPA_WEIGHT_MAX=EPA_WEIGHT_MAX, EPA_FULL_WEIGHT_GAMES=EPA_FULL_WEIGHT_GAMES,
    TOTAL_SHRINK_GAMES=TOTAL_SHRINK_GAMES,
)

# NFL: picked BEFORE any NFL backtest from common published conventions.
# Home field has shrunk in recent seasons; ~1.5 points is a typical modern estimate.
NFL = _Sport(
    NAME="nfl", ELO_START_FBS=1500.0, ELO_START_OTHER=1500.0, ELO_K=20.0,
    ELO_PER_POINT=25.0, SEASON_REGRESSION=1 / 3, HOME_FIELD_POINTS=1.5,
    PLAYS_PER_GAME=62, EPA_WEIGHT_MAX=0.4, EPA_FULL_WEIGHT_GAMES=6, TOTAL_SHRINK_GAMES=4,
)
NFL_START_SEASON = 2012          # ratings start here (all teams 1500)
NFL_FIT_SEASONS = list(range(2015, 2023))  # weather/rest/travel effects are fitted here only
NFL_TEST_SEASONS = [2023, 2024, 2025]
WIND_THRESHOLD_MPH = 10          # wind below this is treated as no effect
# Odds API books for NFL. 10 named books = the credit cost of one region.
# Pinnacle is the sharp reference only (odds.SHARP_BOOKS); the rest are bettable US books.
NFL_ODDS_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us",
                  "espnbet", "fanatics", "betrivers", "bovada", "betonlineag"]

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
