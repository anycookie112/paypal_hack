"""The group's settings: a closed set, each with a default, validated by code (docs/SPEC.md §5).

Only settings changed from their default are stored (the Setting table). Budgets live on Group and Category,
caps and restock intervals on Item.
"""

import json
from dataclasses import dataclass

from sqlmodel import Session, select

from app.domain.money import cents_to_str
from app.models import Group, Setting


class SettingError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    label: str
    kind: str          # cents | int | bool | choice
    default: object
    low: int = 0
    high: int = 0
    choices: tuple[str, ...] = ()


SETTINGS: dict[str, Spec] = {
    "hard_ceiling_cents": Spec("Most any one purchase may cost (above it: the group's normal process)", "cents", 100_00,
                               low=1_00, high=10_000_00),
    "member_monthly_limit_cents": Spec("Most one member may spend per month", "cents", 100_00, low=0, high=10_000_00),
    "waiting_days": Spec("Days between a receipt and its payout", "int", 3, low=0, high=30),
    "report_extension_days": Spec("Days a reported claim's hold is extended by", "int", 4, low=1, high=30),
    "pay_after_extension": Spec("Pay a reported claim when its extended hold ends, unless the treasurer rejected it",
                                "bool", True),
    "on_it_hours": Spec("Hours to buy a confirmed item, when the member doesn't say", "int", 4, low=1, high=72),
    "on_it_max_hours": Spec("Most hours a member can take to buy a confirmed item", "int", 72, low=1, high=168),
    "too_soon_percent": Spec("Ask an approver if an item is bought again within this % of its usual restock time",
                             "int", 50, low=0, high=100),
    "feed_shows_amounts": Spec("Show amounts in the feed", "bool", True),
    "feed_style": Spec("Feed posts", "choice", "each", choices=("each", "daily")),
}


def normalize(key: str, value) -> object:
    """A valid value for `key`, or SettingError."""
    spec = SETTINGS.get(key)
    if spec is None:
        raise SettingError(f"unknown setting '{key}'")
    if spec.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1", "on")
        return bool(value)
    if spec.kind == "choice":
        if value not in spec.choices:
            raise SettingError(f"{key} must be one of {', '.join(spec.choices)}")
        return value
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingError(f"{key} must be a whole number")
    if not spec.low <= number <= spec.high:
        raise SettingError(f"{key} must be between {spec.low} and {spec.high}")
    return number


def get_all(session: Session, group: Group) -> dict[str, object]:
    """Every setting: the group's value where it changed one, else the default."""
    values = {key: spec.default for key, spec in SETTINGS.items()}
    for row in session.exec(select(Setting).where(Setting.group_id == group.id)):
        if row.key in SETTINGS:
            values[row.key] = row.value
    return values


def set_value(session: Session, group: Group, key: str, value) -> object:
    value = normalize(key, value)
    row = session.exec(select(Setting).where(Setting.group_id == group.id, Setting.key == key)).first()
    if row is None:
        row = Setting(group_id=group.id, key=key, value_json="null")
    row.value_json = json.dumps(value)
    session.add(row)
    session.commit()
    return value


def describe(key: str, value) -> str:
    """The setting in plain words, for screens and for the AI ("Most one member may spend per month: $100.00")."""
    spec = SETTINGS[key]
    shown = cents_to_str(value) if spec.kind == "cents" else ("yes" if value is True else "no" if value is False
                                                              else str(value))
    return f"{spec.label}: {shown}"
