"""Requests: telling the group "I'm getting X", and approvers deciding on the ones the rules don't confirm.

Every action takes the Member doing it (docs/SPEC.md §14): a web button, a chat command or an AI tool all call
these same functions. They record feed posts and notifications; the web app lists them, the chat bot will send them.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core import settings
from app.core.decisions import Decision, decide, expire_stale
from app.core.states import move
from app.domain.money import cents_to_str, range_to_str
from app.models import FeedPost, Group, Item, Member, Notification, Request, utcnow


class ActionError(ValueError):
    """The action isn't allowed. The message is shown to the member as-is."""


class NeedsInfo(ActionError):
    """The action can go ahead once the member says more. `missing` is what to ask them, decided by the rules (the AI
    asks for exactly these, one at a time)."""

    def __init__(self, missing: list[str], why: str = ""):
        self.missing = missing
        super().__init__(f"Missing: {'; '.join(missing)}." + (f" {why}" if why else ""))


@dataclass
class Submitted:
    decision: Decision
    request: Request | None  # None when someone is already on it: nothing is recorded


STATUS_FOR = {"confirmed": "on_it", "needs_approval": "needs_approval", "declined": "declined"}


def name_key(name: str) -> str:
    """Names that count as the same: case, punctuation and a plural "s" don't matter ("Glove" = "gloves")."""
    return " ".join(w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", name.lower()))


def thing(session: Session, request: Request) -> str:
    """What the request is for, in words: the item's name, or what they described."""
    if request.item_id:
        return session.get(Item, request.item_id).name
    return request.description or "something"


def submit_request(session: Session, member: Member, *, item_id: int | None = None, description: str = "",
                   quantity: int = 1, estimate_cents: int | None = None, estimate_low_cents: int | None = None,
                   reason: str = "", insist: bool = False, until: datetime | None = None,
                   now: datetime | None = None) -> Submitted:
    """`member` tells the group they're getting something. Decided by the rules (app/core/decisions.py).
    `estimate_cents` is the most it might cost and `estimate_low_cents` the least (a range, app/core/prices.py; None:
    the same). `until` is when they'll have it by ("by 1 pm", app/core/times.py); the group's usual time when they
    don't say.

    Anything a PIC must approve needs a reason, and something not on the list a price range too: every missing detail
    comes back at once (NeedsInfo), so the member is asked for each before the PICs see it."""
    now = now or utcnow()
    group = session.get(Group, member.group_id)
    item = None
    if item_id is not None:
        item = session.get(Item, item_id)
        if item is None or item.group_id != group.id or not item.active:
            raise ActionError(f"No common item with id {item_id}.")
    elif not description.strip():
        raise NeedsInfo(["what they're getting"])
    if quantity < 1:
        raise ActionError("Quantity must be at least 1.")
    config = settings.get_all(session, group)
    hours = _hours_to(until, now, config) if until else None
    expire_stale(session, group.id, now)
    if earlier := _already_asked(session, member, item_id, description):
        where = ("waiting for a PIC" if earlier.status == "needs_approval" else
                 f"on it until {earlier.on_it_until:%a %d %b, %H:%M} UTC")
        raise ActionError(f"{member.name} already asked for this: request {earlier.id} ({thing(session, earlier)}, "
                          f"{where}). Add to that request instead, or release it first.")

    decision = decide(session, group, member, item=item, estimate_cents=estimate_cents,
                      estimate_low_cents=estimate_low_cents, insist=insist, now=now)
    if decision.outcome == "already_on_it":
        return Submitted(decision, None)
    missing = (["a price range"] if item is None and estimate_cents is None else []) +               (["why it's needed"] if decision.outcome == "needs_approval" and not reason.strip() else [])
    if missing:
        raise NeedsInfo(missing, f"A PIC has to approve it: {' '.join(decision.reasons)}")
    low = estimate_low_cents if estimate_low_cents is not None else estimate_cents

    status = STATUS_FOR[decision.outcome]
    request = Request(group_id=group.id, member_id=member.id, item_id=item_id, description=description.strip(),
                      quantity=quantity, estimate_cents=estimate_cents, estimate_low_cents=low,
                      reason=reason.strip(), status="",
                      reasons_json=json.dumps(decision.reasons), cap_cents=decision.cap_cents, hours=hours,
                      created_at=now)
    if status == "on_it":
        request.decided_at = now
        request.on_it_until = until or now + timedelta(hours=config["on_it_hours"])
    move(session, request, status, by=member, now=now)

    if request.status == "on_it":
        _post_on_it(session, request, member)
    elif request.status == "needs_approval":
        estimate = f" ({range_to_str(low, estimate_cents)})" if estimate_cents else ""
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
    request.cap_cents = cap
    request.decided_by_id, request.decided_at = by.id, now
    move(session, request, "on_it", by=by, now=now)
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
    request.decided_by_id, request.decided_at = by.id, now
    move(session, request, "rejected", by=by, now=now)
    notify(session, session.get(Member, request.member_id), "rejected",
            f"{by.name} didn't approve {thing(session, request)}." + (f" {note.strip()}" if note.strip() else ""),
            request_id=request.id)
    session.commit()
    session.refresh(request)
    return request


def change_time(session: Session, request_id: int, by: Member, until: datetime, *,
                now: datetime | None = None) -> Request:
    """The member needs a different time to get it ("by 11", "I need till tonight"): `until`, within the group's most.
    Only their own confirmed request."""
    now = now or utcnow()
    request = _own(session, request_id, by)
    expire_stale(session, request.group_id, now)
    session.refresh(request)
    if request.status != "on_it":
        raise ActionError(f"This request is {request.status.replace('_', ' ')}: there's no time to change.")
    config = settings.get_all(session, session.get(Group, request.group_id))
    request.hours, request.on_it_until = _hours_to(until, now, config), until
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def add_reason(session: Session, request_id: int, by: Member, reason: str) -> Request:
    """More on why it's needed, for the PIC deciding: added to the member's own request while it waits for a PIC,
    and the PICs are told."""
    request = _own(session, request_id, by)
    if request.status != "needs_approval":
        raise ActionError(f"This request is {request.status.replace('_', ' ')}: no PIC is deciding on it.")
    if not reason.strip():
        raise ActionError("Say why it's needed.")
    request.reason = "; ".join(part for part in (request.reason, reason.strip()) if part)
    session.add(request)
    for approver in approvers(session, session.get(Group, request.group_id), besides=by):
        notify(session, approver, "reason_added",
               f"{by.name} added why they need {thing(session, request)}: \"{reason.strip()}\"", request_id=request.id)
    session.commit()
    session.refresh(request)
    return request


def release_request(session: Session, request_id: int, by: Member) -> Request:
    """"I can't make it after all": the item is free for someone else. Only the person getting it can release it,
    so nobody takes someone else's job. A request still waiting for an approver can be withdrawn the same way."""
    request = _own(session, request_id, by)
    if request.status not in ("on_it", "needs_approval"):
        raise ActionError(f"This request is already {request.status.replace('_', ' ')}.")
    move(session, request, "released", by=by)
    session.commit()
    session.refresh(request)
    return request


# --- helpers --------------------------------------------------------------------------------------

def _hours_to(until: datetime, now: datetime, config: dict) -> int:
    """Whole hours from now to the member's deadline, rounded up, checked against the group's most. Kept on the request
    so an approved one gets the same time from when it's approved."""
    if until <= now:
        raise ActionError("That time has already passed: ask them for a later one.")
    most = config["on_it_max_hours"]
    if until - now > timedelta(hours=most):
        raise ActionError(f"That's more than the {most} hours anyone can take to get it: ask them for an earlier time.")
    return math.ceil((until - now) / timedelta(hours=1))


def _already_asked(session: Session, member: Member, item_id: int | None, description: str) -> Request | None:
    """The member's own open request for the same thing: the same item, or a description whose words take in the
    other's ("Folding table" and "Folding table for the market stall"). Asking again would put it in front of the
    PICs twice (a live run did, to add a reason)."""
    mine = session.exec(select(Request).where(Request.member_id == member.id,
                                              Request.status.in_(("on_it", "needs_approval")))).all()
    words = set(name_key(description).split())
    for request in mine:
        if item_id is not None and request.item_id == item_id:
            return request
        if item_id is None and request.item_id is None:
            theirs = set(name_key(request.description).split())
            if words and theirs and (words <= theirs or theirs <= words):
                return request
    return None


def _own(session: Session, request_id: int, by: Member) -> Request:
    """`by`'s own request: nobody changes or releases someone else's."""
    request = session.get(Request, request_id)
    if request is None or request.group_id != by.group_id:
        raise ActionError(f"No request with id {request_id}.")
    if request.member_id != by.id:
        raise ActionError(f"Only {session.get(Member, request.member_id).name} can change that request.")
    return request


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
