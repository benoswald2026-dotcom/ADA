"""Optional hand-entered injuries.csv -> point adjustments to the spread.

Columns: team, player, position, status, impact_points, and optionally entered
impact_points = how many points of margin the team LOSES if the player is
fully out (a positive number; e.g. a starting QB might be 4-7).
The status scales it: out = 100%, doubtful = 75%, questionable = 50%,
probable = 10%. Anything else counts as 0.
entered = when you typed the row in (YYYY-MM-DD or YYYY-MM-DD HH:MM, Eastern).
Rows older than MAX_AGE_HOURS are ignored: by then the market has priced the
news, and applying it again on top of the market would count it twice.
Rows without a date never expire (older files keep working).
"""
import csv
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from names import normalize

STATUS_WEIGHT = {"out": 1.0, "doubtful": 0.75, "questionable": 0.5, "probable": 0.1}
REQUIRED = {"team", "player", "position", "status", "impact_points"}
MAX_AGE_HOURS = 48
ET = ZoneInfo("America/New_York")


def parse_entered(text):
    """'2026-10-14' or '2026-10-14 09:30' (Eastern) -> aware datetime. Blank -> None."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ET)
        except ValueError:
            pass
    raise ValueError(f"injuries.csv: can't read entered date {text!r} (use YYYY-MM-DD or YYYY-MM-DD HH:MM)")


def injury_report(path="injuries.csv", now=None, max_age_hours=MAX_AGE_HOURS):
    """Returns (totals, expired, undated).
    totals:  {normalized team name: total points lost} from rows still in force
    expired: [(team, player)] rows skipped because they are too old
    undated: [(team, player)] rows applied that have no entered date"""
    if not os.path.exists(path):
        return {}, [], []
    now = now or datetime.now(ET)
    cutoff = now - timedelta(hours=max_age_hours)
    totals, expired, undated = {}, [], []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        for row in reader:
            who = (row["team"].strip(), row["player"].strip())
            entered = parse_entered(row.get("entered"))
            if entered is None:
                undated.append(who)
            elif entered < cutoff:
                expired.append(who)
                continue
            weight = STATUS_WEIGHT.get(row["status"].strip().lower(), 0.0)
            pts = float(row["impact_points"] or 0) * weight
            key = normalize(row["team"])
            totals[key] = totals.get(key, 0.0) + pts
    return totals, expired, undated


def load_injuries(path="injuries.csv", now=None):
    """Returns {normalized team name: total points lost}. Missing file -> {}."""
    return injury_report(path, now)[0]


def spread_adjustment(injuries, home, away):
    """Change to the predicted HOME margin (home points minus away points).
    Home injuries lower it; away injuries raise it."""
    return injuries.get(normalize(away), 0.0) - injuries.get(normalize(home), 0.0)
