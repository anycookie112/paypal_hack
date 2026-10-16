"""Turning what a member said about time ("by 11", "1:30 pm", "in 2 hours") into the exact deadline, by code.

The AI passes the member's own words, never a number it worked out itself: live tests had the model round "by 11" at
8:40 down to 2 hours, and invent "10 hours" when the member hadn't said a time at all. The deadline is exactly what
they said ("by 11" is 11:00), so the app and the member never disagree about it. Times are in UTC for now (the group
has no timezone setting yet).
"""

import re
from datetime import datetime, timedelta

from app.core.requests import ActionError

_DURATION = re.compile(r"^(?:in|within|for)?\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)$")
_CLOCK = re.compile(r"^(?:by|at|before|until|till|til)?\s*(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?m\.?)?$")
_WORDS = {"noon": 12, "midday": 12}


def deadline_from(said: str, now: datetime) -> datetime:
    """The time `said` means, after `now`. ActionError if it isn't a time this understands: then the member is asked
    for a clock time."""
    text = " ".join(said.lower().replace("o'clock", "").split()).strip(" .!,")
    tomorrow = False
    if text.endswith(" tomorrow"):
        text, tomorrow = text.removesuffix(" tomorrow").strip(), True
    elif text.startswith("tomorrow "):
        text, tomorrow = text.removeprefix("tomorrow ").strip(), True

    if match := _DURATION.match(text):
        amount, unit = float(match.group(1)), match.group(2)
        return now + timedelta(minutes=amount if unit.startswith("m") else amount * 60)

    if (word := re.sub(r"^(?:by|at|before|until|till|til)\s+", "", text)) in _WORDS:
        target = now.replace(hour=_WORDS[word], minute=0, second=0, microsecond=0)
        return target + (timedelta(days=1) if tomorrow or target <= now else timedelta())

    if not (match := _CLOCK.match(text)):
        raise ActionError(f"Couldn't tell a time from \"{said}\": ask them for a clock time, like \"by 1 pm\".")
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)  # "a" | "p" | None
    if minute > 59 or hour > 23 or (half and not 1 <= hour <= 12):
        raise ActionError(f"\"{said}\" isn't a time of day: ask them for a clock time, like \"by 1 pm\".")
    if half:
        hour = hour % 12 + (12 if half == "p" else 0)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1 if tomorrow else 0)
    candidates = [day + timedelta(hours=hour, minutes=minute)]
    if not half and hour < 12:  # "by 1" at 8:40 means 13:00, not 01:00 tomorrow
        candidates.append(candidates[0] + timedelta(hours=12))
    if not tomorrow:
        candidates += [c + timedelta(days=1) for c in candidates]
    return min(c for c in candidates if c > now)
