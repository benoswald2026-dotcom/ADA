from datetime import datetime

import pytest

import injuries as inj


def _write(tmp_path, rows):
    p = tmp_path / "injuries.csv"
    p.write_text("team,player,position,status,impact_points,entered\n" + "\n".join(rows) + "\n")
    return str(p)


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=inj.ET)


def test_rows_older_than_48_hours_are_ignored(tmp_path):
    path = _write(tmp_path, ["Denver,Old News,QB,out,6,2026-10-13 09:00",
                             "Denver,Fresh News,WR,out,1.5,2026-10-15 20:00"])
    totals, expired, undated = inj.injury_report(path, now=NOW)
    assert totals == {inj.normalize("Denver"): pytest.approx(1.5)}
    assert expired == [("Denver", "Old News")]
    assert undated == []


def test_undated_rows_still_apply_and_are_reported(tmp_path):
    path = _write(tmp_path, ["Seattle,No Date,RB,questionable,2,"])
    totals, expired, undated = inj.injury_report(path, now=NOW)
    assert totals == {inj.normalize("Seattle"): pytest.approx(1.0)}
    assert undated == [("Seattle", "No Date")]


def test_old_files_without_entered_column_still_load(tmp_path):
    p = tmp_path / "injuries.csv"
    p.write_text("team,player,position,status,impact_points\nGeorgia,X,QB,out,6\n")
    assert inj.load_injuries(str(p), now=NOW) == {inj.normalize("Georgia"): pytest.approx(6.0)}


def test_bad_date_is_a_clear_error(tmp_path):
    path = _write(tmp_path, ["Denver,X,QB,out,6,Oct 14"])
    with pytest.raises(ValueError, match="entered date"):
        inj.injury_report(path, now=NOW)
