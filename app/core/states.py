"""Where a request or a claim can go next, and the log of every move (docs/SPEC.md §4).

    move(session, request, "claimed", by=priya)   # checked against the table, logged as a StateChange

Every status change goes through `move`: a move the table doesn't have is a bug and raises. The table also writes
the AI's map of how things move (`lifecycle`), and the log is what changed since the AI's last reply (`moves_since`),
so it can tell "she bought them since" from "I was wrong".
"""

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.models import Claim, Member, Request, StateChange, utcnow

# What each status means, in plain words.
REQUEST_STATES = {
    "needs_approval": "waiting for a PIC",
    "on_it": "getting it",
    "claimed": "bought",
    "expired": "time ran out, not bought",
    "released": "let go",
    "rejected": "a PIC said no",
    "declined": "declined by the rules",
}
CLAIM_STATES = {
    "review": "receipt being checked by a PIC",
    "waiting": "in its hold before the payout",
    "held": "on hold for longer",
    "paying": "being paid through PayPal",
    "paid": "paid back",
    "unclaimed": "payout waiting for a PayPal account",
    "payout_failed": "payout didn't go through",
    "rejected": "won't be paid back",
}

# (from, to): what made it move. "" is a new request or claim.
REQUEST_MOVES = {
    ("", "on_it"): "the rules confirmed it",
    ("", "needs_approval"): "it needs a PIC's yes",
    ("", "declined"): "over the group's limit for one purchase",
    ("needs_approval", "on_it"): "a PIC approved it",
    ("needs_approval", "rejected"): "a PIC turned it down",
    ("needs_approval", "released"): "they withdrew it",
    ("on_it", "claimed"): "they handed in the receipt",
    ("on_it", "expired"): "their time ran out",
    ("on_it", "released"): "they let it go",
    ("expired", "claimed"): "they handed in the receipt after their time ran out",
}
CLAIM_MOVES = {
    ("", "review"): "a check on the receipt failed",
    ("", "waiting"): "the receipt passed every check",
    ("review", "waiting"): "a PIC accepted the receipt",
    ("review", "rejected"): "a PIC rejected the receipt",
    ("waiting", "held"): "someone reported a problem",
    ("waiting", "paying"): "the hold ended",
    ("waiting", "paid"): "nothing left to pay",
    ("waiting", "rejected"): "the treasurer upheld a report",
    ("held", "waiting"): "the treasurer cleared the report",
    ("held", "paying"): "the longer hold ended",
    ("held", "paid"): "nothing left to pay",
    ("held", "rejected"): "the treasurer upheld a report",
    ("paying", "paid"): "PayPal paid it",
    ("paying", "unclaimed"): "no PayPal account at that email yet",
    ("paying", "payout_failed"): "PayPal couldn't pay it",
    ("unclaimed", "paid"): "they signed up to PayPal and got it",
    ("unclaimed", "payout_failed"): "the payout came back unpaid",
    ("unclaimed", "waiting"): "the treasurer took the payout back to pay it again",
    ("payout_failed", "waiting"): "the treasurer took the payout back to pay it again",
}
# Moves only the member, the PICs and the treasurer hear about: a report is private (docs/SPEC.md §4.1).
PRIVATE = {("claim", "waiting", "held"), ("claim", "held", "waiting"), ("claim", "held", "rejected"),
           ("claim", "waiting", "rejected")}

_TABLES = {"request": (REQUEST_STATES, REQUEST_MOVES), "claim": (CLAIM_STATES, CLAIM_MOVES)}


def _kind(record: Request | Claim) -> str:
    return "request" if isinstance(record, Request) else "claim"


def move(session: Session, record: Request | Claim, to: str, *, by: Member | None = None,
         now: datetime | None = None) -> None:
    """Set `record`'s status to `to` and log it. Moving to where it already is does nothing. Not committed."""
    kind, frm = _kind(record), record.status or ""
    if frm == to:
        return
    if (frm, to) not in _TABLES[kind][1]:
        raise ValueError(f"A {kind} can't go from {frm or 'new'} to {to}")
    record.status = to
    session.add(record)
    if record.id is None:
        session.flush()
    session.add(StateChange(group_id=record.group_id, kind=kind, record_id=record.id, from_status=frm, to_status=to,
                            member_id=record.member_id, by_id=by.id if by else None, at=now or utcnow()))


@dataclass
class Moved:
    """One move, for the AI: whose it was, what it's for, and why it moved."""
    at: datetime
    kind: str            # request | claim
    record_id: int
    member: str          # whose request or claim
    what: str            # the item, or what they described
    from_status: str
    to_status: str
    why: str             # what made it move ("they handed in the receipt"); empty when it's private

    def __str__(self) -> str:
        states = _TABLES[self.kind][0]
        before = states.get(self.from_status, "new")
        why = f" ({self.why})" if self.why else ""
        noun = "receipt for " if self.kind == "claim" else ""
        return (f"{self.at:%H:%M} UTC {self.member}'s {noun}{self.what} ({self.kind} {self.record_id}): {before} → "
                f"{states[self.to_status]}{why}")


def moves_since(session: Session, viewer: Member, since: datetime, *, limit: int = 10) -> list[Moved]:
    """What moved in the viewer's group after `since`, oldest first: every request (the whole group sees who's
    getting what), and claims only when they're the viewer's own or the viewer is a PIC. Why a claim was held or
    rejected is only for its member, the PICs and the treasurer."""
    rows = session.exec(select(StateChange).where(StateChange.group_id == viewer.group_id, StateChange.at > since)
                        .order_by(StateChange.at.desc(), StateChange.id.desc()).limit(limit)).all()
    out = []
    for row in reversed(rows):
        own = row.member_id == viewer.id
        if row.kind == "claim" and not (own or viewer.can_approve):
            continue
        private = (row.kind, row.from_status, row.to_status) in PRIVATE
        out.append(_moved(session, row, show_why=not private or own or viewer.can_approve))
    return out


def recent_moves(session: Session, group_id: int, *, limit: int = 30) -> list[Moved]:
    """Every move in the group, newest first, with why: for the Lab page (app/api/lab.py), which shows everything."""
    rows = session.exec(select(StateChange).where(StateChange.group_id == group_id)
                        .order_by(StateChange.at.desc(), StateChange.id.desc()).limit(limit)).all()
    return [_moved(session, row, show_why=True) for row in rows]


def _moved(session: Session, row: StateChange, *, show_why: bool) -> Moved:
    from app.core.requests import thing  # requests imports this module
    record = session.get(Request if row.kind == "request" else Claim, row.record_id)
    request = record if row.kind == "request" else session.get(Request, record.request_id)
    why = _TABLES[row.kind][1][(row.from_status, row.to_status)]
    return Moved(row.at, row.kind, row.record_id, session.get(Member, row.member_id).name, thing(session, request),
                 row.from_status, row.to_status, why if show_why else "")


def lifecycle() -> str:
    """The <lifecycle> section of the coordinator's prompt: every status and where it can go next, written from the
    tables above, so the prompt and the code can't disagree."""
    lines = ["<lifecycle>",
             "How requests and receipts (claims) move. Each line: a status, what it means, and what can happen next."]
    for kind, label in (("request", "Requests:"), ("claim", "Claims (receipts):")):
        states, moves = _TABLES[kind]
        lines.append(label)
        for status, meaning in states.items():
            nexts = [f"{states[to]} ({to}) when {why}" for (frm, to), why in moves.items() if frm == status]
            lines.append(f"- {status}: {meaning}." + (f" Next: {'; '.join(nexts)}." if nexts else " Final."))
    return "\n".join(lines + ["</lifecycle>"])
