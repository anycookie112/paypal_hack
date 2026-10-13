"""The demo group for app/core tests: Riverside Volunteers, four people, the starter list."""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.defaults import create_group
from app.core.requests import submit_request
from app.models import Claim, Group, Item, Member, Request

NOW = datetime(2026, 10, 17, 8, 40, tzinfo=timezone.utc)  # Saturday morning, the cleanup starts at 9
PEOPLE = [("Priya", "member"), ("Marco", "member"), ("Dana", "approver"), ("Sam", "admin")]


def make_group(session: Session) -> Group:
    group = create_group(session, "Riverside Volunteers")
    for name, role in PEOPLE:
        session.add(Member(group_id=group.id, name=name, email=f"{name.lower()}@example.com", role=role))
    session.commit()
    return group


def member(session, name) -> Member:
    return session.exec(select(Member).where(Member.name == name)).one()


def item(session, name) -> Item:
    return session.exec(select(Item).where(Item.name == name)).one()


def past_purchase(session, who: str, what: str, total_cents: int, days_ago: float) -> Claim:
    """A purchase that already happened: a claimed request and its accepted claim."""
    buyer, bought = member(session, who), item(session, what)
    when = NOW - timedelta(days=days_ago)
    request = Request(group_id=buyer.group_id, member_id=buyer.id, item_id=bought.id, status="claimed",
                      cap_cents=bought.cap_cents, created_at=when)
    session.add(request)
    session.flush()
    claim = Claim(group_id=buyer.group_id, request_id=request.id, member_id=buyer.id, item_id=bought.id,
                  total_cents=total_cents, status="paid", submitted_at=when)
    session.add(claim)
    session.commit()
    return claim


def tell(session, who: str, what: str | None = None, **kwargs):
    """"I'm getting X", with a reason (anything a PIC has to approve needs one)."""
    item_id = item(session, what).id if what else None
    kwargs.setdefault("reason", "Needed for Saturday's cleanup")
    return submit_request(session, member(session, who), item_id=item_id, now=kwargs.pop("now", NOW), **kwargs)
