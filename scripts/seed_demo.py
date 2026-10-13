"""Fill the database with the demo group (docs/SPEC.md §11): four people, the starter list, and past purchases.

    uv run python scripts/seed_demo.py           # adds the demo group to data/app.db (or DATABASE_URL)
    uv run python scripts/seed_demo.py --reset   # deletes everything first

Payouts go to real sandbox accounts where .env has them: Priya uses BUYER_EMAIL (a sandbox personal account);
DEMO_PAYPAL_EMAILS=marco@...,dana@...,sam@... sets the others. Without them, payouts would stay unclaimed.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, select  # noqa: E402

from app.core.defaults import create_group  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Claim, Item, Member, Request, utcnow  # noqa: E402

PEOPLE = [("Priya", "member"), ("Marco", "member"), ("Dana", "approver"), ("Sam", "admin")]

# Past purchases (who, item, total, days ago), chosen so the demo story works:
# gloves were bought 9 days ago (fine to buy again), snacks 2 days ago ("too soon" if someone asks),
# trash bags 16 days ago (they usually last 14: running low).
HISTORY = [
    ("Marco", "Gloves", 18_40, 9),
    ("Dana", "Snacks and water", 22_15, 2),
    ("Priya", "Trash bags", 11_99, 16),
    ("Sam", "Flyer printing", 24_00, 16),
    ("Marco", "Coffee", 14_50, 5),
]


def paypal_emails() -> dict[str, str]:
    others = [e.strip() for e in os.environ.get("DEMO_PAYPAL_EMAILS", "").split(",") if e.strip()]
    emails = {"Priya": os.environ.get("BUYER_EMAIL", "")}
    emails.update(dict(zip(["Marco", "Dana", "Sam"], others)))
    return emails


def seed(session: Session) -> None:
    group = create_group(session, "Riverside Volunteers")
    emails = paypal_emails()
    members = {}
    for name, role in PEOPLE:
        members[name] = Member(group_id=group.id, name=name, email=f"{name.lower()}@example.com",
                               paypal_email=emails.get(name) or f"{name.lower()}@example.com", role=role)
        session.add(members[name])
    session.commit()

    now = utcnow()
    for who, what, total, days_ago in HISTORY:
        buyer = members[who]
        item = session.exec(select(Item).where(Item.group_id == group.id, Item.name == what)).one()
        when = now - timedelta(days=days_ago)
        request = Request(group_id=group.id, member_id=buyer.id, item_id=item.id, status="claimed",
                          cap_cents=item.cap_cents, decided_at=when, created_at=when)
        session.add(request)
        session.flush()
        session.add(Claim(group_id=group.id, request_id=request.id, member_id=buyer.id, item_id=item.id,
                          store="Riverside Hardware" if item.name != "Flyer printing" else "PrintHub",
                          receipt_date=when.date(), total_cents=total, status="paid", submitted_at=when))
    session.commit()
    print(f"Seeded '{group.name}' (id {group.id}): {len(PEOPLE)} people, {len(HISTORY)} past purchases.")
    for name, member in members.items():
        print(f"  {name:<6} {member.role:<9} payouts to {member.paypal_email}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if "--reset" in sys.argv:
        SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as s:
        seed(s)
