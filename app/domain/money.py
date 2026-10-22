from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def to_cents(value) -> int:
    """'12.50', '$12.50', 12.5 -> 1250. Raises ValueError for anything else."""
    if value is None or value == "":
        raise ValueError("price is missing")
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a price")
    if amount < 0:
        raise ValueError("price cannot be negative")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_str(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def range_to_str(low: int | None, high: int) -> str:
    """A member's price range in words: 'about $40.00', 'up to $20.00', '$30.00–$50.00'."""
    if low is None or low == high:
        return f"about {cents_to_str(high)}"
    if low == 0:
        return f"up to {cents_to_str(high)}"
    return f"{cents_to_str(low)}–{cents_to_str(high)}"
