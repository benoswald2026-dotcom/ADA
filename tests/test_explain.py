import pytest

from explain import EXAMPLE, explain


def test_readme_worked_example_numbers():
    d = explain(EXAMPLE)
    assert d["pred"]["elo_margin"] == pytest.approx(5.3)
    assert d["pred"]["epa_margin"] == pytest.approx(9.0)
    assert d["pred"]["margin"] == pytest.approx(6.78)
    assert d["pred"]["home_prob"] == pytest.approx(0.726, abs=5e-4)
    assert d["consensus"] == pytest.approx(0.647, abs=5e-4)
    assert d["best"]["side"] == "home"
    assert d["best"]["edge"] == pytest.approx(0.070, abs=5e-4)
    assert d["best"]["flag"] is True


def test_readme_injury_twist_turns_flag_into_pass():
    d = explain({**EXAMPLE, "injury_away": 5.0})
    assert d["pred"]["home_prob"] == pytest.approx(0.845, abs=5e-4)
    assert d["best"]["flag"] is False  # gap to market ~19.8 pts > 10
