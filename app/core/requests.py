"""Requests: telling the group "I'm getting X", and approvers deciding on the ones the rules don't confirm.

Every action takes the Member doing it (docs/SPEC.md §14): a web button, a chat command or an AI tool all call
these same functions. They record feed posts and notifications; the web app lists them, the chat bot will send them.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core import settings
from app.core.decisions import Decision, decide
from app.domain.money import cents_to_str
from app.models import FeedPost, Group, Item, Member, Notification, Request, utcnow


class ActionError(ValueError):
    """The action isn't allowed. The message is shown to the member as-is."""


@dataclass
class Submitted:
    decision: Decision
    request: Request | None  # None when someone is already on it: nothing is recorded


STATUS_FOR = {"confirmed": "on_it", "needs_approval": "needs_approval", "declined": "declined"}


def thing(session: Session, request: Request) -> str:
    """What the request is for, in words: the item's name, or what they described."""
    if request.item_id:
        return session.get(Item, request.item_id).name
    return request.description or "something"


def submit_request(session: Session, member: Member, *, item_id: int | None = None, description: str = "",
                   quantity: int = 1, estimate_cents: int | None = None, reason: str = "", insist: bool = False,
                   hours: int | None = None, now: datetime | None = None) -> Submitted:
    """`member` tells the group they're getting something. Decided by the rules (app/core/decisions.py).
    `hours` is how long they need to get it ("by 1 pm"); the group's default when they don't say."""
    now = now or utcnow()
    group = session.get(Group, member.group_id)
    item = None
    if item_id is not None:
        item = session.get(Item, item_id)
        if item is None or item.group_id != group.id or not item.active:
            raise ActionError(f"No common item with id {item_id}.")
    elif not description.strip():
        raise ActionError("Say what you're getting.")
    if quantity < 1:
        raise ActionError("Quantity must be at least 1.")
    config = settings.get_all(session, group)
    if hours is not None and not 1 <= hours <= config["on_it_max_hours"]:
        raise ActionError(f"Pick between 1 and {config['on_it_max_hours']} hours to get it.")

    decision = decide(session, group, member, item=item, estimate_cents=estimate_cents, insist=insist, now=now)
    if decision.outcome == "already_on_it":
        return Submitted(decision, None)
    if decision.outcome == "needs_approval" and not reason.strip():
        raise ActionError(f"This needs a PIC to approve it: {' '.join(decision.reasons)} Say why it's needed.")

    request = Request(group_id=group.id, member_id=member.id, item_id=item_id, description=description.strip(),
                      quantity=quantity, estimate_cents=estimate_cents, reason=reason.strip(),
                      status=STATUS_FOR[decision.outcome], reasons_json=json.dumps(decision.reasons),
                      cap_cents=decision.cap_cents, hours=hours, created_at=now)
    if request.status == "on_it":
        request.decided_at = now
        request.on_it_until = now + timedelta(hours=hours or config["on_it_hours"])
    session.add(request)
    session.flush()

    if request.status == "on_it":
        _post_on_it(session, request, member)
    elif request.status == "needs_approval":
        estimate = f" (about {cents_to_str(estimate_cents)})" if estimate_cents else ""
        for approver in approvers(session, group, besides=member):
            notify(session, approver, "needs_approval",
                    f"{member.name} wants to get {thing(session, request)}{estimate}. " + " ".join(decision.reasons),
                    request_id=request.id)
    session.commit()
    session.refresh(request)
    return Submitted(decision, request)


def approve_request(session: Session, request_id: int, by: Member, *, cap_cents: int | None = None,
                    now: datetime | None = None) -> Request:
    """An approver says yes. `cap_cents` is the most that will be paid back (default: what the rules worked out)."""
    now = now or utcnow()
    request = _pending(session, request_id, by)
    cap = cap_cents if cap_cents is not None else request.cap_cents
    if not cap or cap <= 0:
        raise ActionError("Set the most that will be paid back for this.")
    config = settings.get_all(session, session.get(Group, request.group_id))
    ceiling = config["hard_ceiling_cents"]
    if cap > ceiling:
        raise ActionError(f"Over the {cents_to_str(ceiling)} limit for one purchase: this needs the group's "
                          "normal process.")
    request.status, request.cap_cents = "on_it", cap
    request.decided_by_id, request.decided_at = by.id, now
    request.on_it_until = now + timedelta(hours=request.hours or config["on_it_hours"])
    session.add(request)
    requester = session.get(Member, request.member_id)
    _post_on_it(session, request, requester)
    notify(session, requester, "approved",
            f"{by.name} approved {thing(session, request)}, up to {cents_to_str(cap)}. Go ahead!",
            request_id=request.id)
    session.commit()
    session.refresh(request)
    return request


def reject_request(session: Session, request_id: int, by: Member, *, note: str = "",
                   now: datetime | None = None) -> Request:
    now = now or utcnow()
    request = _pending(session, request_id, by)
    request.status, request.decided_by_id, request.decided_at = "rejected", by.id, now
    session.add(request)
    notify(session, session.get(Member, request.member_id), "rejected",
            f"{by.name} didn't approve {thing(session, request)}." + (f" {note.strip()}" if note.strip() else ""),
            request_id=request.id)
    session.commit()
    session.refresh(request)
    return request


def release_request(session: Session, request_id: int, by: Member) -> Request:
    """"I can't make it after all": the item is free for someone else. Only the person getting it can release it,
    so nobody takes someone else's job. A request still waiting for an approver can be withdrawn the same way."""
    request = session.get(Request, request_id)
    if request is None or request.group_id != by.group_id:
        raise ActionError(f"No request with id {request_id}.")
    if request.member_id != by.id:
        raise ActionError(f"Only {session.get(Member, request.member_id).name} can release it.")
    if request.status not in ("on_it", "needs_approval"):
        raise ActionError(f"This request is already {request.status.replace('_', ' ')}.")
    request.status = "released"
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


# --- helpers --------------------------------------------------------------------------------------

def _pending(session: Session, request_id: int, by: Member) -> Request:
    request = session.get(Request, request_id)
    if request is None or request.group_id != by.group_id:
        raise ActionError(f"No request with id {request_id}.")
    if not by.can_approve:
        raise ActionError("Only approvers can decide on requests.")
    if request.member_id == by.id:
        raise ActionError("You can't approve your own request: ask another approver.")
    if request.status != "needs_approval":
        raise ActionError(f"This request is already {request.status.replace('_', ' ')}.")
    return request


def approvers(session: Session, group: Group, *, besides: Member | None = None) -> list[Member]:
    """The PICs (approvers and admins), except `besides`."""
    members = session.exec(select(Member).where(Member.group_id == group.id, Member.active)).all()
    return [m for m in members if m.can_approve and (besides is None or m.id != besides.id)]


def admins(session: Session, group: Group, *, besides: Member | None = None) -> list[Member]:
    """The treasurer(s): reports and money problems go only to them."""
    members = session.exec(select(Member).where(Member.group_id == group.id, Member.active,
                                                Member.role == "admin")).all()
    return [m for m in members if besides is None or m.id != besides.id]


def _post_on_it(session: Session, request: Request, member: Member) -> None:
    session.add(FeedPost(group_id=request.group_id, request_id=request.id, member_id=member.id, kind="on_it",
                         text=f"{member.name} is getting {thing(session, request)}"))


def notify(session: Session, member: Member, kind: str, text: str, **payload) -> None:
    session.add(Notification(member_id=member.id, kind=kind, text=text, payload_json=json.dumps(payload)))
