"""What the treasurer sets up (docs/SPEC.md §5): the common items and their categories, the group's budget, and its
settings. Everyone can see them; only admins change them.

Items are switched off, never deleted: past requests and claims still point at them. A change applies to what's asked
for next: a confirmed request keeps the cap it was confirmed with.

Names have to be told apart: the AI finds items by the words in their names and other names (memory.find_items),
so two active items can't share a name, and "Glove" counts as the same name as "Gloves".
"""

import re
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core import settings
from app.core.requests import ActionError
from app.domain.money import cents_to_str
from app.models import Category, Group, Item, Member

PROOFS = ("receipt", "receipt_photo")
MAX_RESTOCK_DAYS = 365
MAX_BUDGET_CENTS = 100_000_00
MAX_NAME = 60


class NotAllowed(ActionError):
    """Only an admin can change this."""


# --- what everyone sees ----------------------------------------------------------------------------

@dataclass
class CatalogItem:
    id: int
    name: str
    aliases: list[str]
    cap_cents: int
    restock_days: int
    proof: str
    active: bool


@dataclass
class CatalogCategory:
    id: int
    name: str
    monthly_budget_cents: int  # 0 = no category budget
    items: list[CatalogItem]


@dataclass
class Catalog:
    monthly_budget_cents: int  # the group's; 0 = none
    hard_ceiling_cents: int    # the most any cap can be
    categories: list[CatalogCategory]


@dataclass
class SettingView:
    key: str
    label: str
    kind: str                  # cents | int | bool | choice
    value: object
    default: object
    low: int
    high: int
    choices: list[str]


@dataclass
class GroupSettings:
    name: str
    currency: str
    monthly_budget_cents: int
    settings: list[SettingView]


def catalog(session: Session, group: Group) -> Catalog:
    """Every category and item, switched-off ones too, by name."""
    items = session.exec(select(Item).where(Item.group_id == group.id).order_by(Item.name)).all()
    categories = session.exec(select(Category).where(Category.group_id == group.id).order_by(Category.id)).all()
    return Catalog(
        monthly_budget_cents=group.monthly_budget_cents,
        hard_ceiling_cents=settings.get_all(session, group)["hard_ceiling_cents"],
        categories=[CatalogCategory(c.id, c.name, c.monthly_budget_cents,
                                    [_view(i) for i in items if i.category_id == c.id]) for c in categories])


def group_settings(session: Session, group: Group) -> GroupSettings:
    values = settings.get_all(session, group)
    return GroupSettings(group.name, group.currency, group.monthly_budget_cents,
                         [SettingView(key, spec.label, spec.kind, values[key], spec.default, spec.low, spec.high,
                                      list(spec.choices)) for key, spec in settings.SETTINGS.items()])


def _view(item: Item) -> CatalogItem:
    return CatalogItem(item.id, item.name, _aliases(item.aliases), item.cap_cents, item.restock_days, item.proof,
                       item.active)


# --- categories ------------------------------------------------------------------------------------

def add_category(session: Session, by: Member, *, name: str, monthly_budget_cents: int = 0) -> Category:
    group = _admin_group(session, by)
    category = Category(group_id=group.id, name=_name(name), monthly_budget_cents=_budget(monthly_budget_cents))
    _check_category_name(session, category)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


def update_category(session: Session, by: Member, category_id: int, *, name: str | None = None,
                    monthly_budget_cents: int | None = None) -> Category:
    group = _admin_group(session, by)
    category = _category(session, group, category_id)
    if name is not None:
        category.name = _name(name)
        _check_category_name(session, category)
    if monthly_budget_cents is not None:
        category.monthly_budget_cents = _budget(monthly_budget_cents)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


def _check_category_name(session: Session, category: Category) -> None:
    for other in session.exec(select(Category).where(Category.group_id == category.group_id,
                                                     Category.id != category.id)):
        if _key(other.name) == _key(category.name):
            raise ActionError(f"There's already a category called {other.name}.")


# --- items -----------------------------------------------------------------------------------------

def add_item(session: Session, by: Member, *, category_id: int, name: str, cap_cents: int, restock_days: int = 14,
             proof: str = "receipt", aliases: list[str] | None = None) -> Item:
    group = _admin_group(session, by)
    item = Item(group_id=group.id, category_id=_category(session, group, category_id).id, name=_name(name),
                cap_cents=cap_cents, restock_days=restock_days, proof=proof)
    item.aliases = ", ".join(_clean_aliases(aliases or [], item.name))
    _check_item(session, group, item)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def update_item(session: Session, by: Member, item_id: int, *, category_id: int | None = None, name: str | None = None,
                aliases: list[str] | None = None, cap_cents: int | None = None, restock_days: int | None = None,
                proof: str | None = None, active: bool | None = None) -> Item:
    """Change an item, or switch it off (`active=False`) or back on. Requests already confirmed keep their cap."""
    group = _admin_group(session, by)
    item = session.get(Item, item_id)
    if item is None or item.group_id != group.id:
        raise ActionError(f"No item with id {item_id}.")
    if category_id is not None:
        item.category_id = _category(session, group, category_id).id
    if name is not None:
        item.name = _name(name)
    if aliases is not None or name is not None:
        item.aliases = ", ".join(_clean_aliases(aliases if aliases is not None else _aliases(item.aliases),
                                                item.name))
    for field, value in (("cap_cents", cap_cents), ("restock_days", restock_days), ("proof", proof),
                         ("active", active)):
        if value is not None:
            setattr(item, field, value)
    try:
        _check_item(session, group, item)
    except ActionError:
        session.rollback()  # the item is attached: undo the changes made to it above
        raise
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _check_item(session: Session, group: Group, item: Item) -> None:
    ceiling = settings.get_all(session, group)["hard_ceiling_cents"]
    if not 0 < item.cap_cents <= ceiling:
        raise ActionError(f"{item.name}'s limit must be between $0.01 and {cents_to_str(ceiling)} "
                          "(the most any one purchase can cost).")
    if not 1 <= item.restock_days <= MAX_RESTOCK_DAYS:
        raise ActionError(f"How long {item.name} lasts must be between 1 and {MAX_RESTOCK_DAYS} days.")
    if item.proof not in PROOFS:
        raise ActionError(f"Proof must be one of {', '.join(PROOFS)}.")
    others = session.exec(select(Item).where(Item.group_id == group.id, Item.id != item.id)).all()
    for other in others:
        if _key(other.name) == _key(item.name):
            off = " (switched off: switch it back on instead)" if not other.active else ""
            raise ActionError(f"There's already an item called {other.name}{off}.")
    if not item.active:
        return
    # Only active items have to be told apart: switched-off ones aren't offered to the AI.
    names = {_key(n): other.name for other in others if other.active
             for n in [other.name, *_aliases(other.aliases)]}
    for name in [item.name, *_aliases(item.aliases)]:
        if _key(name) in names:
            raise ActionError(f"\"{name}\" is already a name for {names[_key(name)]}.")


# --- the group and its settings --------------------------------------------------------------------

def save_settings(session: Session, by: Member, *, name: str | None = None, monthly_budget_cents: int | None = None,
                  values: dict[str, object] | None = None) -> GroupSettings:
    """Change the group's name, budget and settings together: all of them are checked before any is saved."""
    group = _admin_group(session, by)
    try:
        new = {key: settings.normalize(key, value) for key, value in (values or {}).items()}
    except settings.SettingError as error:
        raise ActionError(str(error)) from None
    merged = settings.get_all(session, group) | new
    if merged["on_it_hours"] > merged["on_it_max_hours"]:
        raise ActionError(f"The default time to buy ({merged['on_it_hours']} h) can't be more than the most "
                          f"({merged['on_it_max_hours']} h).")
    over = session.exec(select(Item).where(Item.group_id == group.id, Item.active,
                                           Item.cap_cents > merged["hard_ceiling_cents"]).order_by(Item.name)).all()
    if over:
        raise ActionError(f"{', '.join(i.name for i in over)} can cost more than "
                          f"{cents_to_str(merged['hard_ceiling_cents'])}: lower their limits first.")
    if name is not None:
        group.name = _name(name)
    if monthly_budget_cents is not None:
        group.monthly_budget_cents = _budget(monthly_budget_cents)
    session.add(group)
    session.commit()
    for key, value in new.items():
        settings.set_value(session, group, key, value)
    return group_settings(session, group)


# --- helpers ---------------------------------------------------------------------------------------

def _admin_group(session: Session, member: Member) -> Group:
    if member.role != "admin" or not member.active:
        raise NotAllowed("Only the treasurer (an admin) can change the group's setup.")
    return session.get(Group, member.group_id)


def _category(session: Session, group: Group, category_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None or category.group_id != group.id:
        raise ActionError(f"No category with id {category_id}.")
    return category


def _name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ActionError("Give it a name.")
    if len(name) > MAX_NAME:
        raise ActionError(f"Keep the name under {MAX_NAME} characters.")
    return name


def _budget(cents: int) -> int:
    if not 0 <= cents <= MAX_BUDGET_CENTS:
        raise ActionError(f"A monthly budget must be between $0 (none) and {cents_to_str(MAX_BUDGET_CENTS)}.")
    return cents


def _aliases(text: str) -> list[str]:
    return [a.strip() for a in text.split(",") if a.strip()]


def _clean_aliases(aliases: list[str], name: str) -> list[str]:
    """Other names, tidied: no blanks, no repeats, not the item's own name."""
    seen, cleaned = {_key(name)}, []
    for alias in aliases:
        alias = " ".join(alias.replace(",", " ").split())
        if alias and _key(alias) not in seen:
            seen.add(_key(alias))
            cleaned.append(alias)
    return cleaned


def _key(name: str) -> str:
    """Names that count as the same: case, punctuation and a plural "s" don't matter ("Glove" = "gloves")."""
    return " ".join(w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", name.lower()))
