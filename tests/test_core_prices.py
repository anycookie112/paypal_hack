"""What a member said about price, turned into a range by code (app/core/prices.py)."""

import pytest

from app.core.prices import price_range
from app.core.requests import ActionError
from app.domain.money import range_to_str


@pytest.mark.parametrize("said, low, high", [
    ("About $40", 40_00, 40_00),          # one amount is both ends
    ("40 bucks", 40_00, 40_00),
    ("$12.50", 12_50, 12_50),
    ("30 to 50", 30_00, 50_00),
    ("between $50 and $30", 30_00, 50_00),  # either order
    ("$30-$50", 30_00, 50_00),
    ("under 20", 0, 20_00),               # no lower end
    ("no more than $1,200", 0, 1200_00),
])
def test_a_price_is_a_range(said, low, high):
    assert price_range(said) == (low, high)


@pytest.mark.parametrize("said, asked", [
    ("not sure", "ask them roughly what it will cost"),
    ("2 for 10 or 3 for 12", "ask them roughly what it will cost"),  # more than a range
    ("over 40", "ask them roughly the most it could cost"),            # no top end
    ("40+", "ask them roughly the most it could cost"),
])
def test_what_isnt_a_range_asks_again(said, asked):
    with pytest.raises(ActionError, match=asked):
        price_range(said)


def test_a_range_in_words():
    assert [range_to_str(*r) for r in ((None, 40_00), (40_00, 40_00), (0, 20_00), (30_00, 50_00))] == [
        "about $40.00", "about $40.00", "up to $20.00", "$30.00–$50.00"]
