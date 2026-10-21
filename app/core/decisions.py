"""The decision engine: someone says "I'm getting X". Is it confirmed, does it need an approver, or is it out?

Code decides, never the AI (docs/SPEC.md §1, §6). Checks, in order:

    over the hard ceiling                        → declined (the group's normal process)
    someone is already getting it                → already_on_it (unless they insist: then an approver decides)
    not a common item                            → needs_approval
    over the item's cap                          → needs_approval
    bought again too soon                        → needs_approval
    over the member's monthly limit              → needs_approval
    over the category's or the group's budget    → needs_approval
    otherwise                                    → confirmed, up to the item's cap

Spending counts accepted claims this month plus the caps of confirmed requests not claimed yet, so many people
confirmed at once can't overspend a budget between them.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, select

from app.core import settings
from app.core.states import move
from app.domain.money import cents_to_str
from app.models import Category, Claim, Group, Item, Member, Request, utcnow

# Purchases: every claim but a rejected one (one a PIC is still checking counts: it was bought, and will likely be paid).
ACCEPTED_CLAIMS = ("review", "waiting", "held", "paying", "paid", "unclaimed", "payout_failed")

Outcome = Literal["confirmed", "needs_approval", "declined", "already_on_it"]


@dataclass
class Decision:
    outcome: Outcome
    reasons: list[str] = field(default_factory=list)  # plain words, written here, shown as-is
    cap_cents: int = 0                                 # the most paid back, when confirmed
    on_it: list[Request] = field(default_factory=list)  # who's already getting it, for already_on_it


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def expire_stale(session: Session, group_id: int, now: datetime | None = None) -> int:
    """Confirmed requests nobody claimed in time become expired, so the item shows as needed again."""
    now = now or utcnow()
    stale = session.exec(select(Request).where(Request.group_id == group_id, Request.status == "on_it",
                                               Request.on_it_until <= now)).all()
    for request in stale:
        move(session, request, "expired", now=now)
    if stale:
        session.commit()
    return len(stale)


def on_it(session: Session, item: Item, now: datetime | None = None) -> list[Request]:
    """Who's getting `item` right now: confirmed requests that haven't been claimed or expired."""
    now = now or utcnow()
    return list(session.exec(select(Request).where(Request.item_id == item.id, Request.status == "on_it",
                                                   Request.on_it_until > now).order_by(Request.created_at)).all())


def last_purchase(session: Session, item: Item) -> Claim | None:
    return session.exec(select(Claim).where(Claim.item_id == item.id, Claim.status.in_(ACCEPTED_CLAIMS))
                        .order_by(Claim.submitted_at.desc())).first()


def committed_cents(session: Session, group: Group, now: datetime, *, member: Member | None = None,
                    category: Category | None = None) -> int:
    """Spent or promised this month: accepted claims plus the caps of confirmed, unclaimed requests."""
    since = month_start(now)
    claims = select(func.coalesce(func.sum(Claim.total_cents), 0)).where(
        Claim.group_id == group.id, Claim.status.in_(ACCEPTED_CLAIMS), Claim.submitted_at >= since)
    pending = select(func.coalesce(func.sum(Request.cap_cents), 0)).where(
        Request.group_id == group.id, Request.status == "on_it", Request.on_it_until > now,
        Request.created_at >= since)
    if member is not None:
        claims = claims.where(Claim.member_id == member.id)
        pending = pending.where(Request.member_id == member.id)
    if category is not None:
        item_ids = select(Item.id).where(Item.category_id == category.id)
        claims = claims.where(Claim.item_id.in_(item_ids))
        pending = pending.where(Request.item_id.in_(item_ids))
    return session.exec(claims).one() + session.exec(pending).one()


def bought_too_soon(item: Item, last_bought: datetime, now: datetime, too_soon_percent: int) -> bool:
    """Getting `item` again now would be too soon: it was bought within `too_soon_percent` of its usual restock time.
    Then an approver decides (and the AI is told it was recently bought)."""
    return now - last_bought < timedelta(days=item.restock_days * too_soon_percent / 100)


def _ago(then: datetime, now: datetime) -> str:
    days = (now - then).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def decide(session: Session, group: Group, member: Member, *, item: Item | None, estimate_cents: int | None = None,
           insist: bool = False, now: datetime | None = None) -> Decision:
    """Decide on "I'm getting `item`" (or something not on the list, when `item` is None).

    `insist=True` means the member wants it even though someone is already on it: an approver decides.
    """
    now = now or utcnow()
    expire_stale(session, group.id, now)
    config = settings.get_all(session, group)
    ceiling = config["hard_ceiling_cents"]
    amount = item.cap_cents if item else estimate_cents

    if estimate_cents is not None and estimate_cents > ceiling:
        return Decision("declined", [f"Over the {cents_to_str(ceiling)} limit for one purchase: "
                                     "this needs the group's normal process."])

    reasons = []
    if item is not None:
        getting = on_it(session, item, now)
        if getting and not insist:
            return Decision("already_on_it", on_it=getting)
        if getting:
            reasons.append(f"Someone is already getting {item.name}.")
        if estimate_cents is not None and estimate_cents > item.cap_cents:
            reasons.append(f"{cents_to_str(estimate_cents)} is over the {cents_to_str(item.cap_cents)} "
                           f"limit for {item.name}.")
            amount = estimate_cents
        last = last_purchase(session, item)
        if last and bought_too_soon(item, last.submitted_at, now, config["too_soon_percent"]):
            buyer = session.get(Member, last.member_id)
            reasons.append(f"{item.name} was bought {_ago(last.submitted_at, now)} by {buyer.name}; "
                           f"it usually lasts {item.restock_days} days.")
    else:
        reasons.append("Not on the group's list of common items.")
        if amount is None:
            reasons.append("No price estimate yet.")

    if amount is not None:
        limit = config["member_monthly_limit_cents"]
        if committed_cents(session, group, now, member=member) + amount > limit:
            reasons.append(f"Over {member.name}'s {cents_to_str(limit)} monthly limit.")
        category = session.get(Category, item.category_id) if item else None
        if category and category.monthly_budget_cents and \
                committed_cents(session, group, now, category=category) + amount > category.monthly_budget_cents:
            reasons.append(f"Over this month's {category.name} budget ({cents_to_str(category.monthly_budget_cents)}).")
        if group.monthly_budget_cents and committed_cents(session, group, now) + amount > group.monthly_budget_cents:
            reasons.append(f"Over the group's monthly budget ({cents_to_str(group.monthly_budget_cents)}).")

    if reasons:
        return Decision("needs_approval", reasons, cap_cents=amount or 0)
    return Decision("confirmed", [f"{item.name} is pre-approved up to {cents_to_str(item.cap_cents)}."],
                    cap_cents=item.cap_cents)
