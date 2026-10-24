"""Direction field fallback: `side` today, `outcome_side` once Kalshi drops the legacy field."""
from kalshi_truth.fills import fill_side


def test_side_is_read_first():
    assert fill_side({"side": "no", "outcome_side": "no"}) == "no"


def test_outcome_side_is_the_fallback():
    assert fill_side({"outcome_side": "yes"}) == "yes"
    assert fill_side({"side": None, "outcome_side": "no"}) == "no"


def test_no_direction_is_none():
    assert fill_side({}) is None
    assert fill_side({"side": "maybe"}) is None
