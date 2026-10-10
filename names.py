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
