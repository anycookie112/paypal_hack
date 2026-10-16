"""What a member said about time, turned into the exact deadline by code (app/core/times.py)."""

from datetime import timedelta

import pytest

from app.core.requests import ActionError
from app.core.times import deadline_from
from tests.demo import NOW  # Sat 17 Oct, 08:40 UTC

TODAY = NOW.replace(hour=0, minute=0)


@pytest.mark.parametrize("said, hours_after_midnight", [
    ("by 11", 11),              # exactly 11:00: what they said, not rounded
    ("11", 11),
    ("11am", 11),
    ("by 11:30 a.m.", 11.5),
    ("by 1", 13),               # 13:00, not 01:00 tomorrow
    ("1 pm", 13),
    ("13:00", 13),
    ("before 8", 20),           # 08:00 has passed: 20:00
    ("noon", 12),
    ("by noon", 12),
    ("tomorrow 9am", 24 + 9),
    ("by 11 tomorrow", 24 + 11),
    ("By 11!", 11),
])
def test_a_time_of_day_is_the_exact_deadline(said, hours_after_midnight):
    assert deadline_from(said, NOW) == TODAY + timedelta(hours=hours_after_midnight)


@pytest.mark.parametrize("said, minutes", [("in 2 hours", 120), ("2h", 120), ("within 90 minutes", 90),
                                           ("30 mins", 30)])
def test_a_duration_counts_from_now(said, minutes):
    assert deadline_from(said, NOW) == NOW + timedelta(minutes=minutes)


@pytest.mark.parametrize("said", ["after lunch", "soon", "25:00", "13pm", "11:75"])
def test_anything_else_asks_for_a_clock_time(said):
    with pytest.raises(ActionError, match="clock time"):
        deadline_from(said, NOW)
