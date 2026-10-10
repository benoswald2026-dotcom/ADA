"""Optional hand-entered injuries.csv -> point adjustments to the spread.

Columns: team, player, position, status, impact_points
impact_points = how many points of margin the team LOSES if the player is
fully out (a positive number; e.g. a starting QB might be 4-7).
The status scales it: out = 100%, doubtful = 75%, questionable = 50%,
probable = 10%. Anything else counts as 0.
"""
import csv
import os

from names import normalize

STATUS_WEIGHT = {"out": 1.0, "doubtful": 0.75, "questionable": 0.5, "probable": 0.1}
REQUIRED = {"team", "player", "position", "status", "impact_points"}


def load_injuries(path="injuries.csv"):
    """Returns {normalized team name: total points lost}. Missing file -> {}."""
    if not os.path.exists(path):
        return {}
    totals = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        for row in reader:
            weight = STATUS_WEIGHT.get(row["status"].strip().lower(), 0.0)
            pts = float(row["impact_points"] or 0) * weight
            key = normalize(row["team"])
            totals[key] = totals.get(key, 0.0) + pts
    return totals


def spread_adjustment(injuries, home, away):
    """Change to the predicted HOME margin (home points minus away points).
    Home injuries lower it; away injuries raise it."""
    return injuries.get(normalize(away), 0.0) - injuries.get(normalize(home), 0.0)
