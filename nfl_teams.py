"""Static NFL team facts: names (to match The Odds API and Kalshi), time zone,
and home-stadium coordinates (for the weather forecast).

west = hours west of US Eastern time during the season (Arizona does not use
daylight saving, so in Sept-Jan it lines up with Pacific time).
"""
from names import normalize

TEAMS = {
    "ARI": ("Arizona", "Cardinals", 3, 33.5276, -112.2626),
    "ATL": ("Atlanta", "Falcons", 0, 33.7554, -84.4008),
    "BAL": ("Baltimore", "Ravens", 0, 39.2780, -76.6227),
    "BUF": ("Buffalo", "Bills", 0, 42.7738, -78.7870),
    "CAR": ("Carolina", "Panthers", 0, 35.2258, -80.8528),
    "CHI": ("Chicago", "Bears", 1, 41.8623, -87.6167),
    "CIN": ("Cincinnati", "Bengals", 0, 39.0955, -84.5161),
    "CLE": ("Cleveland", "Browns", 0, 41.5061, -81.6995),
    "DAL": ("Dallas", "Cowboys", 1, 32.7473, -97.0945),
    "DEN": ("Denver", "Broncos", 2, 39.7439, -105.0201),
    "DET": ("Detroit", "Lions", 0, 42.3400, -83.0456),
    "GB": ("Green Bay", "Packers", 1, 44.5013, -88.0622),
    "HOU": ("Houston", "Texans", 1, 29.6847, -95.4107),
    "IND": ("Indianapolis", "Colts", 0, 39.7601, -86.1639),
    "JAX": ("Jacksonville", "Jaguars", 0, 30.3239, -81.6373),
    "KC": ("Kansas City", "Chiefs", 1, 39.0489, -94.4839),
    "LV": ("Las Vegas", "Raiders", 3, 36.0909, -115.1833),
    "LAC": ("Los Angeles", "Chargers", 3, 33.9535, -118.3392),
    "LA": ("Los Angeles", "Rams", 3, 33.9535, -118.3392),
    "MIA": ("Miami", "Dolphins", 0, 25.9580, -80.2389),
    "MIN": ("Minnesota", "Vikings", 1, 44.9737, -93.2577),
    "NE": ("New England", "Patriots", 0, 42.0909, -71.2643),
    "NO": ("New Orleans", "Saints", 1, 29.9511, -90.0812),
    "NYG": ("New York", "Giants", 0, 40.8135, -74.0745),
    "NYJ": ("New York", "Jets", 0, 40.8135, -74.0745),
    "PHI": ("Philadelphia", "Eagles", 0, 39.9008, -75.1675),
    "PIT": ("Pittsburgh", "Steelers", 0, 40.4468, -80.0158),
    "SF": ("San Francisco", "49ers", 3, 37.4030, -121.9700),
    "SEA": ("Seattle", "Seahawks", 3, 47.5952, -122.3316),
    "TB": ("Tampa Bay", "Buccaneers", 0, 27.9759, -82.5033),
    "TEN": ("Tennessee", "Titans", 1, 36.1665, -86.7713),
    "WAS": ("Washington", "Commanders", 0, 38.9076, -76.8645),
}

# Relocated franchises: old code -> current code (ratings carry over), and
# the time zone the team actually played in back then.
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "JAC": "JAX"}
OLD_WEST = {"OAK": 3, "SD": 3, "STL": 1}


def canon(code):
    return RELOCATED.get(code, code)


def west_hours(code):
    """Hours west of Eastern for the team's home city at the time of the game."""
    if code in OLD_WEST:
        return OLD_WEST[code]
    return TEAMS[canon(code)][2]


def full_name(code):
    city, nick = TEAMS[canon(code)][:2]
    return f"{city} {nick}"


OLD_NAMES = {"WAS": ("Washington Redskins", "Washington Football Team")}   # some sites never updated


def aliases(code):
    """Every way an API might write this team, normalized."""
    city, nick = TEAMS[canon(code)][:2]
    out = {normalize(code), normalize(nick), normalize(f"{city} {nick}")}
    if city in ("New York", "Los Angeles"):
        out.add(normalize(f"{city} {nick[0]}"))        # Kalshi-style "New York G"
        out.add(normalize(f"{'NY' if city == 'New York' else 'LA'} {nick}"))
    else:
        out.add(normalize(city))
    out |= {normalize(n) for n in OLD_NAMES.get(canon(code), ())}
    return out


def code_for(name):
    """Map any team name ('Green Bay Packers', 'Green Bay', 'GB') to a code, or None."""
    n = normalize(name)
    hits = [c for c in TEAMS if n in aliases(c)]
    return hits[0] if len(hits) == 1 else None
