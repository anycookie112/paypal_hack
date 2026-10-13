"""What the treasurer sets up: common items, categories, budgets and settings (app/core/setup.py)."""

import pytest
from sqlmodel import select

from app.core import settings
from app.core.memory import find_items
from app.core.requests import ActionError
from app.core.setup import (NotAllowed, add_category, add_item, catalog, group_settings, save_settings,
                            update_category, update_item)
from app.models import Category
from tests.demo import item, member, tell


def category_id(session, name):
    return session.exec(select(Category.id).where(Category.name == name)).one()


# --- who can change it ---------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["Priya", "Dana"])  # a member, and an approver who isn't the treasurer
def test_only_an_admin_changes_the_setup(session, group, who):
    with pytest.raises(NotAllowed):
        update_item(session, member(session, who), item(session, "Gloves").id, cap_cents=30_00)
    with pytest.raises(NotAllowed):
        add_category(session, member(session, who), name="Tools")
    with pytest.raises(NotAllowed):
        save_settings(session, member(session, who), monthly_budget_cents=500_00)
    assert item(session, "Gloves").cap_cents == 25_00


def test_everyone_sees_the_catalog_with_switched_off_items(session, group):
    update_item(session, member(session, "Sam"), item(session, "Coffee").id, active=False)
    seen = catalog(session, group)
    assert seen.monthly_budget_cents == 300_00 and seen.hard_ceiling_cents == 100_00
    supplies = seen.categories[0]
    assert (supplies.name, supplies.monthly_budget_cents) == ("Supplies", 120_00)
    assert [i.name for i in supplies.items] == ["Gloves", "Tape and markers", "Trash bags"]
    coffee = next(i for c in seen.categories for i in c.items if i.name == "Coffee")
    assert coffee.active is False and coffee.aliases == ["coffee", "tea"]


# --- items -----------------------------------------------------------------------------------------

def test_an_added_item_can_be_found_and_confirmed(session, group):
    added = add_item(session, member(session, "Sam"), category_id=category_id(session, "Supplies"),
                     name="  Zip   ties ", cap_cents=8_00, restock_days=30, aliases=["cable ties", " ", "zip ties"])
    assert (added.name, added.aliases) == ("Zip ties", "cable ties")
    assert [i.name for i in find_items(session, group, "cable ties")] == ["Zip ties"]
    assert tell(session, "Priya", "Zip ties").request.cap_cents == 8_00


def test_a_switched_off_item_is_not_offered_and_cant_be_asked_for(session, group):
    gloves = item(session, "Gloves")
    update_item(session, member(session, "Sam"), gloves.id, active=False)
    assert "Gloves" not in [i.name for i in find_items(session, group, "gloves")]
    with pytest.raises(ActionError, match="No common item"):
        tell(session, "Priya", "Gloves")
    update_item(session, member(session, "Sam"), gloves.id, active=True)
    assert tell(session, "Priya", "Gloves").decision.outcome == "confirmed"


def test_a_new_cap_applies_to_new_requests_only(session, group):
    confirmed = tell(session, "Priya", "Gloves").request
    update_item(session, member(session, "Sam"), item(session, "Gloves").id, cap_cents=30_00)
    session.refresh(confirmed)
    again = tell(session, "Marco", "Gloves", insist=True).request  # a PIC decides, at the new cap
    assert (confirmed.cap_cents, again.cap_cents) == (25_00, 30_00)


@pytest.mark.parametrize("change, error", [
    ({"cap_cents": 0}, "between \\$0.01 and \\$100.00"),
    ({"cap_cents": 150_00}, "between \\$0.01 and \\$100.00"),
    ({"restock_days": 0}, "between 1 and 365 days"),
    ({"proof": "selfie"}, "Proof must be one of"),
    ({"name": "   "}, "Give it a name"),
    ({"name": "Trash bag"}, "already an item called Trash bags"),
    ({"aliases": ["Bin bags"]}, "\"Bin bags\" is already a name for Trash bags"),
    ({"category_id": 999}, "No category with id 999"),
])
def test_an_item_that_breaks_a_rule_is_not_saved(session, group, change, error):
    gloves = item(session, "Gloves")
    with pytest.raises(ActionError, match=error):
        update_item(session, member(session, "Sam"), gloves.id, **change)
    session.refresh(gloves)
    assert (gloves.name, gloves.cap_cents, gloves.restock_days, gloves.proof) == ("Gloves", 25_00, 14, "receipt_photo")


def test_a_switched_off_item_keeps_its_name_but_frees_its_other_names(session, group):
    sam = member(session, "Sam")
    update_item(session, sam, item(session, "Coffee").id, active=False)
    with pytest.raises(ActionError, match="switched off: switch it back on instead"):
        add_item(session, sam, category_id=category_id(session, "Food and drink"), name="Coffees", cap_cents=5_00)
    add_item(session, sam, category_id=category_id(session, "Food and drink"), name="Tea", cap_cents=5_00)
    with pytest.raises(ActionError, match="\"tea\" is already a name for Tea"):
        update_item(session, sam, item(session, "Coffee").id, active=True)


# --- categories and the group ----------------------------------------------------------------------

def test_categories_have_their_own_names_and_budgets(session, group):
    sam = member(session, "Sam")
    tools = add_category(session, sam, name="Tools", monthly_budget_cents=40_00)
    assert update_category(session, sam, tools.id, monthly_budget_cents=0).monthly_budget_cents == 0
    with pytest.raises(ActionError, match="already a category called Supplies"):
        update_category(session, sam, tools.id, name="supplies")
    with pytest.raises(ActionError, match="between \\$0 \\(none\\)"):
        add_category(session, sam, name="Rent", monthly_budget_cents=-1)


def test_settings_are_all_checked_before_any_is_saved(session, group):
    sam = member(session, "Sam")
    with pytest.raises(ActionError, match="must be between 1 and 168"):
        save_settings(session, sam, monthly_budget_cents=500_00, values={"waiting_days": 5, "on_it_max_hours": 0})
    assert group.monthly_budget_cents == 300_00 and settings.get_all(session, group)["waiting_days"] == 3

    saved = save_settings(session, sam, name="Riverside Crew", monthly_budget_cents=500_00,
                          values={"waiting_days": "5", "feed_shows_amounts": False})
    assert (saved.name, saved.monthly_budget_cents) == ("Riverside Crew", 500_00)
    values = {s.key: s.value for s in saved.settings}
    assert values["waiting_days"] == 5 and values["feed_shows_amounts"] is False


@pytest.mark.parametrize("values, error", [
    ({"on_it_hours": 10, "on_it_max_hours": 8}, "can't be more than the most"),
    ({"hard_ceiling_cents": 20_00}, "Flyer printing, Gloves, Snacks and water can cost more than \\$20.00"),
    ({"feed_style": "weekly"}, "feed_style must be one of each, daily"),
])
def test_settings_that_dont_fit_together_are_refused(session, group, values, error):
    with pytest.raises(ActionError, match=error):
        save_settings(session, member(session, "Sam"), values=values)


def test_the_settings_view_lists_every_setting_with_its_default(session, group):
    view = group_settings(session, group)
    assert [s.key for s in view.settings] == list(settings.SETTINGS)
    ceiling = next(s for s in view.settings if s.key == "hard_ceiling_cents")
    assert (ceiling.kind, ceiling.value, ceiling.default, ceiling.low) == ("cents", 100_00, 100_00, 1_00)
