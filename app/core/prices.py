"""Turning what a member said about price ("about $40", "30 to 50", "under 20") into a range, by code.

Like times (app/core/times.py), the AI passes the member's own words and code works out the amounts, so the PICs see
what the member actually said. A rough range is enough: the rules use the high end, and a PIC sets what's paid back.
"""

import re
from decimal import Decimal

from app.core.requests import ActionError

_AMOUNT = re.compile(r"\d+(?:\.\d{1,2})?")
_AT_MOST = re.compile(r"\b(under|below|less than|up to|at most|no more than|max|maximum)\b")
_AT_LEAST = re.compile(r"\b(over|above|more than|at least|min|minimum)\b|\d\s*\+")


def price_range(said: str) -> tuple[int, int]:
    """(low, high) in cents. One amount is both ends; "under 20" is 0 to 20. ActionError if there's no amount, more
    than two, or no top end ("over 40"): then the member is asked for a rough range."""
    text = said.lower().replace(",", "")
    amounts = [int(Decimal(a) * 100) for a in _AMOUNT.findall(text)]
    if not amounts or len(amounts) > 2:
        raise ActionError(f"Couldn't tell a price from \"{said}\": ask them roughly what it will cost (a range is "
                          "fine).")
    if len(amounts) == 2:
        low, high = sorted(amounts)
        return low, high
    [amount] = amounts
    if _AT_MOST.search(text):  # first: "no more than" is a top end
        return 0, amount
    if _AT_LEAST.search(text):
        raise ActionError(f"\"{said}\" has no top end: ask them roughly the most it could cost.")
    return amount, amount
