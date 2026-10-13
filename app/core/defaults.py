"""A new group starts with a starter list, so setup takes minutes (docs/SPEC.md §5). The treasurer edits it."""

from sqlmodel import Session

from app.models import Category, Group, Item

# category -> (monthly budget in cents, [(item, aliases, cap in cents, restock days, proof)])
STARTER = {
    "Supplies": (120_00, [
        ("Gloves", "work gloves, disposable gloves, cleanup gloves", 25_00, 14, "receipt_photo"),
        ("Trash bags", "bin bags, garbage bags, bin liners", 15_00, 14, "receipt"),
        ("Tape and markers", "tape, markers, sharpies, pens", 10_00, 30, "receipt"),
    ]),
    "Food and drink": (100_00, [
        ("Snacks and water", "snacks, water, drinks, fruit", 30_00, 7, "receipt"),
        ("Coffee", "coffee, tea", 20_00, 14, "receipt"),
    ]),
    "Printing": (50_00, [
        ("Flyer printing", "flyers, posters, printing, sign-up sheets", 25_00, 30, "receipt"),
    ]),
    "Cleaning and first aid": (30_00, [
        ("Cleaning wipes", "wipes, hand sanitizer, soap", 10_00, 30, "receipt"),
        ("First-aid restock", "plasters, bandages, band-aids, first aid", 20_00, 60, "receipt"),
    ]),
}


def create_group(session: Session, name: str, *, monthly_budget_cents: int = 300_00, starter: bool = True) -> Group:
    group = Group(name=name.strip(), monthly_budget_cents=monthly_budget_cents)
    session.add(group)
    session.flush()
    if starter:
        for category_name, (budget, items) in STARTER.items():
            category = Category(group_id=group.id, name=category_name, monthly_budget_cents=budget)
            session.add(category)
            session.flush()
            for item_name, aliases, cap, restock_days, proof in items:
                session.add(Item(group_id=group.id, category_id=category.id, name=item_name, aliases=aliases,
                                 cap_cents=cap, restock_days=restock_days, proof=proof))
    session.commit()
    session.refresh(group)
    return group
