"""Team-name matching across APIs.

CFBD says "Ohio State", The Odds API says "Ohio State Buckeyes", and Kalshi
may say "Ohio St." These helpers put names into one comparable form.
"""
import re


def normalize(name):
    """'Ohio St.' -> 'ohio state'   'Miami (OH)' -> 'miami oh'   'Texas A&M' -> 'texas a&m'"""
    s = (name or "").lower().replace("é", "e")
    s = re.sub(r"[().']", " ", s)
    s = re.sub(r"\bst\b", "state", s)
    return " ".join(s.split())


def odds_name_to_school(odds_name, teams):
    """Map an Odds API name ('Texas A&M Aggies') to a CFBD school ('Texas A&M').

    1) exact 'school mascot' match, else
    2) the LONGEST school name that the Odds API name starts with
       (so 'Texas A&M Aggies' picks 'Texas A&M', not 'Texas').
    Returns None if nothing matches.
    """
    target = normalize(odds_name)
    for t in teams:
        if normalize(f"{t['school']} {t['mascot']}") == target:
            return t["school"]
    best = None
    for t in teams:
        school = normalize(t["school"])
        if target == school or target.startswith(school + " "):
            if best is None or len(school) > len(normalize(best)):
                best = t["school"]
    return best


# Schools that Kalshi, CFBD and The Odds API spell differently even after
# normalize(). Every spelling in a group maps to one key, so matching works
# whichever spelling a source uses. Keys and spellings are normalized forms.
_ALIAS_GROUPS = {
    "app state": ["appalachian state", "app state"],
    "miami": ["miami fl", "miami", "miami florida", "miami-florida"],
    "miami oh": ["miami oh", "miami-ohio", "miami ohio"],
    "usc": ["usc", "southern california"],
    "ucf": ["ucf", "central florida", "central florida ucf"],
    "louisiana": ["louisiana", "louisiana-lafayette", "ul lafayette"],
    "southern university": ["southern university", "southern u", "southern u."],
    "ul monroe": ["louisiana-monroe", "louisiana monroe", "ul monroe", "ulm", "louisianamonroe ulm"],
    "umass": ["umass", "massachusetts"],
    "uconn": ["uconn", "connecticut"],
    "southern miss": ["southern miss", "southern mississippi"],
    "ole miss": ["ole miss", "mississippi"],
    "fiu": ["fiu", "florida international"],
    "fau": ["fau", "florida atlantic"],
    "nc state": ["nc state", "north carolina state"],
    "sam houston": ["sam houston", "sam houston state"],
    "hawaii": ["hawai i", "hawaii"],
    "ut martin": ["tennessee-martin", "ut martin"],
    "albany": ["university at albany", "albany"],
    "utrgv": ["ut rio grande valley", "utrgv"],
}
_ALIASES = {spelling: key for key, spellings in _ALIAS_GROUPS.items() for spelling in spellings}


def team_key(name):
    """normalize() plus the alias table: 'Appalachian St.' and 'App State' -> 'app state'."""
    n = normalize(name)
    return _ALIASES.get(n, n)
