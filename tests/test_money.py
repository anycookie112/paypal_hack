import pytest

from app.domain.money import cents_to_str, to_cents


@pytest.mark.parametrize("value,cents", [("12.50", 1250), ("$1,200", 120000), (0.59, 59), ("3", 300)])
def test_to_cents(value, cents):
    assert to_cents(value) == cents


@pytest.mark.parametrize("value", ["", None, "abc", "-1"])
def test_to_cents_rejects(value):
    with pytest.raises(ValueError):
        to_cents(value)


def test_cents_to_str():
    assert cents_to_str(2450) == "$24.50" and cents_to_str(120000) == "$1,200.00"
