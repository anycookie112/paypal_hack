"""Holding and releasing payouts: the 3-day hold, reports, and paying people back with PayPal (docs/SPEC.md §4, §7).

    start_hold         a claim was accepted: its payout is due after the hold (default 3 days)
    ask_buyer          step 1, "I can't find the gloves": asks the person who bought them. No hold, no treasurer.
    answer_question    the buyer answers ("they're in the van"); the asker is told
    report_problem     step 2, the formal report, saying what was checked: the hold is extended, the treasurer told
    resolve_report     the treasurer decides: nothing wrong (pay) or a problem (reject, and it goes on record)
    approve_extra      the PIC approves the over-budget part of a claim (paid separately, after the base)
    run_due            the background job: pay what's due, send what didn't go out, follow up on PayPal's processing
    cancel_unclaimed   take back a payout nobody accepted (no PayPal account at that email), to pay it again

Money leaves in one place (`_send`, the only caller of PayPal's create_payout), and every movement is a LedgerEntry.
PayPal can't pull back a payout once it's received, so nothing is sent until the hold is over (§7).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.core import settings
from app.core.requests import ActionError, admins, approvers, notify, thing
from app.domain.money import cents_to_str
from app.models import Claim, Group, LedgerEntry, Member, Notification, Payout, Report, Request, utcnow
from app.payments.paypal import PayPalClient, PayPalError, cents

SETTLED = {"SUCCESS"}
CHARGED = {"SUCCESS", "UNCLAIMED"}  # PayPal takes its fee for these
FAILED = {"RETURNED", "FAILED", "BLOCKED", "REFUNDED", "REVERSED", "DENIED"}  # the money is back with the group
PROCESSING = {"PENDING", "NEW", "ONHOLD", "UNCLAIMED"}  # keep checking (an unclaimed payout returns after 30 days)


# --- the ledger -----------------------------------------------------------------------------------

def balance_cents(session: Session, group: Group) -> int:
    """The group's money: top-ups minus payouts and fees, plus anything PayPal returned."""
    return session.exec(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
                        .where(LedgerEntry.group_id == group.id)).one()


def record_top_up(session: Session, group: Group, amount_cents: int, *, paypal_ref: str = "",
                  note: str = "") -> LedgerEntry:
    if amount_cents <= 0:
        raise ActionError("A top-up must be more than $0.")
    entry = LedgerEntry(group_id=group.id, kind="top_up", amount_cents=amount_cents, paypal_ref=paypal_ref, note=note)
    session.add(entry)
    session.commit()
    return entry


def _ledger_once(session: Session, payout: Payout, claim: Claim, kind: str, amount_cents: int) -> None:
    """One ledger entry per payout and kind, however often PayPal's status is checked."""
    exists = session.exec(select(LedgerEntry).where(LedgerEntry.payout_id == payout.id,
                                                    LedgerEntry.kind == kind)).first()
    if exists is None and amount_cents:
        session.add(LedgerEntry(group_id=claim.group_id, kind=kind, amount_cents=amount_cents, claim_id=claim.id,
                                payout_id=payout.id, paypal_ref=payout.paypal_item_id))


# --- holds and reports ----------------------------------------------------------------------------

def start_hold(session: Session, claim: Claim, now: datetime | None = None) -> Claim:
    """The claim was accepted: pay it back after the hold. What's paid is the total, up to the request's budget."""
    now = now or utcnow()
    group = session.get(Group, claim.group_id)
    request = session.get(Request, claim.request_id)
    if not claim.base_cents:
        claim.base_cents = min(claim.total_cents, request.cap_cents)
    claim.status = "waiting"
    claim.payout_due_at = now + timedelta(days=settings.get_all(session, group)["waiting_days"])
    session.add(claim)
    session.commit()
    session.refresh(claim)
    return claim


def ask_buyer(session: Session, claim_id: int, by: Member, text: str, now: datetime | None = None) -> Report:
    """Step 1: "I can't find the gloves". The person who bought them is asked where they are. Nothing else happens:
    no hold, nothing to the treasurer. If it isn't sorted, the formal report is step 2."""
    now = now or utcnow()
    claim = _claim(session, claim_id, by)
    if claim.member_id == by.id:
        raise ActionError("That's your own purchase.")
    if not text.strip():
        raise ActionError("Say what you're asking about.")
    owner = session.get(Member, claim.member_id)
    what = thing(session, session.get(Request, claim.request_id))
    question = Report(group_id=claim.group_id, claim_id=claim.id, member_id=by.id, kind="question",
                      text=text.strip(), created_at=now)
    session.add(question)
    session.flush()
    notify(session, owner, "question",
           f"{by.name} is looking for the {what} you got on {claim.submitted_at:%a %d %b}: \"{question.text}\". "
           "Where did you put it?", claim_id=claim.id, report_id=question.id)
    session.commit()
    session.refresh(question)
    return question


def answer_question(session: Session, report_id: int, by: Member, answer: str) -> Report:
    """The buyer answers a question about their purchase; whoever asked is told."""
    question = session.get(Report, report_id)
    if question is None or question.group_id != by.group_id or question.kind != "question":
        raise ActionError(f"No question with id {report_id}.")
    claim = session.get(Claim, question.claim_id)
    if claim.member_id != by.id:
        raise ActionError("Only the person who bought it can answer.")
    if not answer.strip():
        raise ActionError("Say where it is.")
    question.answer, question.status = answer.strip(), "answered"
    session.add(question)
    what = thing(session, session.get(Request, claim.request_id))
    notify(session, session.get(Member, question.member_id), "question_answered",
           f"{by.name} answered about the {what}: \"{question.answer}\"", claim_id=claim.id, report_id=question.id)
    session.commit()
    session.refresh(question)
    return question


def report_problem(session: Session, claim_id: int, by: Member, text: str, *, checked: str,
                   now: datetime | None = None) -> Report:
    """Step 2, the formal report: what's wrong, and what was checked first (the place it's kept, asking the buyer).
    It goes privately to the treasurer, never into the feed. While the claim is in its hold, the payout is held and
    the hold extended; after the payout, it's recorded for the treasurer (the money can't be pulled back)."""
    now = now or utcnow()
    claim = _claim(session, claim_id, by)
    if claim.member_id == by.id:
        raise ActionError("You can't report your own claim.")
    if not text.strip():
        raise ActionError("Say what's wrong.")
    if not checked.strip():
        raise ActionError("Say what you checked first, e.g. where it's kept, or asking the person who bought it.")
    group = session.get(Group, claim.group_id)
    owner = session.get(Member, claim.member_id)
    what = thing(session, session.get(Request, claim.request_id))
    report = Report(group_id=group.id, claim_id=claim.id, member_id=by.id, kind="report", text=text.strip(),
                    checked=checked.strip(), created_at=now)
    session.add(report)
    session.flush()

    if claim.status in ("waiting", "held"):
        extension = timedelta(days=settings.get_all(session, group)["report_extension_days"])
        claim.status = "held"
        claim.payout_due_at = max(claim.payout_due_at or now, now + extension)
        session.add(claim)
        held = f"The payout is on hold until {claim.payout_due_at:%a %d %b}."
        notify(session, owner, "claim_reported",
               f"Someone filed a report about your {what}, so the treasurer will take a look. Your payout is on hold "
               f"until {claim.payout_due_at:%a %d %b}.", claim_id=claim.id, report_id=report.id)
    else:
        held = "It was already paid out, so this is recorded for you to follow up."
    for treasurer in _treasurers(session, group, besides=owner):
        notify(session, treasurer, "report",
               f"{by.name} reported a problem with {owner.name}'s {what} ({cents_to_str(claim.total_cents)}): "
               f"\"{report.text}\" They checked: {report.checked}. {held}", claim_id=claim.id, report_id=report.id)
    session.commit()
    session.refresh(report)
    return report


def resolve_report(session: Session, report_id: int, by: Member, *, problem: bool, note: str = "",
                   now: datetime | None = None) -> Report:
    """The treasurer decides. `problem=False`: nothing wrong, the claim is paid as soon as no other report is open.
    `problem=True`: the claim is rejected if it hasn't been paid yet, and it goes on the member's record either way."""
    now = now or utcnow()
    report = session.get(Report, report_id)
    if report is None or report.group_id != by.group_id or report.kind != "report":
        raise ActionError(f"No report with id {report_id}.")
    claim = session.get(Claim, report.claim_id)
    if by.role != "admin":
        raise ActionError("Only the treasurer can decide on reports.")
    if claim.member_id == by.id:
        raise ActionError("You can't decide on a report about your own claim: ask another admin.")
    if report.status != "open":
        raise ActionError("This report has already been decided.")
    owner = session.get(Member, claim.member_id)
    what = thing(session, session.get(Request, claim.request_id))
    report.status = "resolved"
    report.resolution = note.strip() or ("Problem confirmed." if problem else "No problem found.")
    session.add(report)

    if problem:
        owner.problems_on_record += 1
        session.add(owner)
        if claim.status in ("waiting", "held"):
            claim.status = "rejected"
            if claim.extra_status in ("pending", "approved"):
                claim.extra_status = "rejected"
            notify(session, owner, "claim_rejected",
                   f"The treasurer looked into your {what} and won't pay it back." + (f" {note.strip()}" if note.strip()
                                                                                       else ""), claim_id=claim.id)
    else:
        others_open = session.exec(select(Report).where(Report.claim_id == claim.id, Report.kind == "report",
                                                        Report.status == "open", Report.id != report.id)).first()
        if claim.status == "held" and others_open is None:
            claim.status, claim.payout_due_at = "waiting", now
            notify(session, owner, "claim_cleared", f"All sorted: your {what} will be paid back shortly.",
                   claim_id=claim.id)
    session.add(claim)
    session.commit()
    session.refresh(report)
    return report


# --- over-budget extras ---------------------------------------------------------------------------

def approve_extra(session: Session, claim_id: int, by: Member) -> Claim:
    claim = _extra_pending(session, claim_id, by)
    claim.extra_status = "approved"
    session.add(claim)
    notify(session, session.get(Member, claim.member_id), "extra_approved",
           f"{by.name} approved the extra {cents_to_str(claim.extra_cents)} for your "
           f"{thing(session, session.get(Request, claim.request_id))}. It's paid after the rest.", claim_id=claim.id)
    session.commit()
    session.refresh(claim)
    return claim


def reject_extra(session: Session, claim_id: int, by: Member, *, note: str = "") -> Claim:
    claim = _extra_pending(session, claim_id, by)
    claim.extra_status = "rejected"
    session.add(claim)
    notify(session, session.get(Member, claim.member_id), "extra_rejected",
           f"{by.name} didn't approve the extra {cents_to_str(claim.extra_cents)}; the rest is still paid back."
           + (f" {note.strip()}" if note.strip() else ""), claim_id=claim.id)
    session.commit()
    session.refresh(claim)
    return claim


# --- releasing: the background job ----------------------------------------------------------------

@dataclass
class RunReport:
    sent: list[int] = field(default_factory=list)      # payout ids sent to PayPal
    waiting: list[str] = field(default_factory=list)   # why something due wasn't sent (no PayPal email, low balance)
    updated: list[int] = field(default_factory=list)   # payout ids whose PayPal status changed
    errors: list[str] = field(default_factory=list)


def due(session: Session, now: datetime | None = None) -> list[tuple[Claim, str]]:
    """What should be paid now: (claim, "base" | "extra")."""
    now = now or utcnow()
    out = []
    for claim in session.exec(select(Claim).where(Claim.status.in_(("waiting", "held")),
                                                  Claim.payout_due_at <= now)).all():
        if claim.status == "held" and not settings.get_all(session, session.get(Group, claim.group_id))[
                "pay_after_extension"]:
            continue  # waits for the treasurer
        out.append((claim, "base"))
    for claim in session.exec(select(Claim).where(Claim.status == "paid", Claim.extra_status == "approved")).all():
        out.append((claim, "extra"))
    return out


async def run_due(session: Session, paypal: PayPalClient, now: datetime | None = None) -> RunReport:
    """Pay what's due, retry payouts that never reached PayPal, and check on the ones PayPal is still processing.
    Safe to run as often as you like: nothing is ever paid twice."""
    now = now or utcnow()
    report = RunReport()
    tried: set[int] = set()
    for claim, kind in due(session, now):
        try:
            payout = _prepare(session, claim, kind, report)
            if payout is not None:
                tried.add(payout.id)
                await _send(session, paypal, payout, report)
        except PayPalError as exc:
            report.errors.append(f"claim {claim.id}: {exc}")
    for payout in session.exec(select(Payout).where(Payout.status == "CREATED")).all():
        if payout.id not in tried:  # prepared earlier but never reached PayPal (e.g. a crash): same sender_batch_id
            try:
                await _send(session, paypal, payout, report)
            except PayPalError as exc:
                report.errors.append(f"payout {payout.id}: {exc}")
    for payout in session.exec(select(Payout).where(Payout.status.in_(PROCESSING))).all():
        try:
            item = await paypal.get_payout_item(payout.paypal_item_id)
            if _apply(session, payout, item):
                report.updated.append(payout.id)
        except PayPalError as exc:
            report.errors.append(f"payout {payout.id}: {exc}")
    session.commit()
    return report


async def cancel_unclaimed(session: Session, paypal: PayPalClient, claim_id: int, by: Member,
                           now: datetime | None = None) -> Claim:
    """Take back a payout sitting unclaimed (that email has no PayPal account), so it can be paid again, e.g. after
    the member fixed their PayPal email. The next run pays it as a new attempt."""
    now = now or utcnow()
    claim = _claim(session, claim_id, by)
    if by.role != "admin":
        raise ActionError("Only the treasurer can cancel a payout.")
    payout = session.exec(select(Payout).where(Payout.claim_id == claim.id, Payout.status == "UNCLAIMED")
                          .order_by(Payout.id.desc())).first()
    if payout is None:
        raise ActionError("There's no unclaimed payout to cancel for this claim.")
    item = await paypal.cancel_payout_item(payout.paypal_item_id)
    _apply(session, payout, item)
    if payout.kind == "base":
        claim.status, claim.payout_due_at = "waiting", now
    else:
        claim.extra_status = "approved"
    session.add(claim)
    session.commit()
    session.refresh(claim)
    return claim


# --- sending and following up ---------------------------------------------------------------------

def _prepare(session: Session, claim: Claim, kind: str, report: RunReport) -> Payout | None:
    """Record the payout we're about to send (or reuse one that never reached PayPal), with its ledger entry.
    Committed before PayPal is called, so a crash in between is retried with the same sender_batch_id."""
    member = session.get(Member, claim.member_id)
    group = session.get(Group, claim.group_id)
    amount = claim.base_cents if kind == "base" else claim.extra_cents
    if amount <= 0:
        _mark(claim, kind, "paid")
        session.add(claim)
        session.commit()
        return None
    if not member.paypal_email:
        _notify_once(session, [member], "needs_paypal_email", claim,
                     f"Add your PayPal email so we can pay you back {cents_to_str(amount)}.")
        report.waiting.append(f"claim {claim.id}: {member.name} has no PayPal email")
        return None
    if balance_cents(session, group) < amount:
        _notify_once(session, _treasurers(session, group), "needs_top_up", claim,
                     f"{member.name} is due {cents_to_str(amount)}, but the group has "
                     f"{cents_to_str(balance_cents(session, group))}. Top up to pay it.")
        report.waiting.append(f"claim {claim.id}: the group's balance is too low")
        return None

    attempts = session.exec(select(Payout).where(Payout.claim_id == claim.id, Payout.kind == kind)
                            .order_by(Payout.attempt.desc())).all()
    if attempts and attempts[0].status == "CREATED":
        return attempts[0]
    attempt = attempts[0].attempt + 1 if attempts else 1
    payout = Payout(claim_id=claim.id, kind=kind, attempt=attempt, amount_cents=amount,
                    sender_batch_id=f"claim-{claim.id}-{kind}-{attempt}")
    session.add(payout)
    session.flush()
    _ledger_once(session, payout, claim, "payout", -amount)
    _mark(claim, kind, "paying")
    session.add(claim)
    session.commit()
    session.refresh(payout)
    return payout


async def _send(session: Session, paypal: PayPalClient, payout: Payout, report: RunReport) -> None:
    claim = session.get(Claim, payout.claim_id)
    member = session.get(Member, claim.member_id)
    group = session.get(Group, claim.group_id)
    what = thing(session, session.get(Request, claim.request_id))
    batch = await paypal.create_payout(payout.sender_batch_id, member.paypal_email, payout.amount_cents,
                                       note=f"{group.name}: {what}" + (" (extra)" if payout.kind == "extra" else ""),
                                       subject=f"{group.name} paid you back", currency=group.currency)
    item = batch["items"][0]
    payout.paypal_batch_id = batch["batch_header"]["payout_batch_id"]
    payout.paypal_item_id = item["payout_item_id"]
    _apply(session, payout, item)
    session.commit()
    report.sent.append(payout.id)


def _apply(session: Session, payout: Payout, item: dict) -> bool:
    """Take PayPal's item status (never the batch's: a batch can say SUCCESS with its item UNCLAIMED)."""
    status = item.get("transaction_status", payout.status)
    changed = status != payout.status
    payout.status, payout.updated_at = status, utcnow()
    session.add(payout)
    claim = session.get(Claim, payout.claim_id)
    member = session.get(Member, claim.member_id)
    group = session.get(Group, claim.group_id)
    what = thing(session, session.get(Request, claim.request_id))
    amount = cents_to_str(payout.amount_cents)

    if status in CHARGED:
        fee = cents(item.get("payout_item_fee"))
        payout.fee_cents = fee
        _ledger_once(session, payout, claim, "payout_fee", -fee)
    if status in SETTLED:
        _mark(claim, payout.kind, "paid")
        _notify_once(session, [member], "paid", claim,
                     f"Paid back {amount} for {what}" + (" (the approved extra)" if payout.kind == "extra" else "")
                     + ". Thanks for getting it!", payout_id=payout.id)
    elif status == "UNCLAIMED":
        if payout.kind == "base":
            claim.status = "unclaimed"
        _notify_once(session, [member], "payout_unclaimed", claim,
                     f"PayPal sent {amount} to {member.paypal_email}, which has no PayPal account yet. Sign up with "
                     "that email within 30 days to receive it, or ask the treasurer to change your PayPal email.",
                     payout_id=payout.id)
    elif status in FAILED:
        _ledger_once(session, payout, claim, "payout_returned", payout.amount_cents)
        if payout.kind == "base":
            claim.status = "payout_failed"
        else:
            claim.extra_status = "approved"  # can be sent again
        _notify_once(session, _treasurers(session, group), "payout_failed", claim,
                     f"The {amount} payout to {member.name} for {what} didn't go through ({status}). The money is back "
                     "in the group's balance.", payout_id=payout.id)
    session.add(claim)
    return changed


# --- helpers --------------------------------------------------------------------------------------

def _mark(claim: Claim, kind: str, status: str) -> None:
    if kind == "base":
        claim.status = status
    else:
        claim.extra_status = status


def _claim(session: Session, claim_id: int, by: Member) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None or claim.group_id != by.group_id:
        raise ActionError(f"No claim with id {claim_id}.")
    return claim


def _extra_pending(session: Session, claim_id: int, by: Member) -> Claim:
    claim = _claim(session, claim_id, by)
    if not by.can_approve:
        raise ActionError("Only a PIC can approve extras.")
    if claim.member_id == by.id:
        raise ActionError("You can't approve your own extra: ask another PIC.")
    if claim.extra_status != "pending":
        raise ActionError("There's no extra waiting for approval on this claim.")
    return claim


def _treasurers(session: Session, group: Group, *, besides: Member | None = None) -> list[Member]:
    """Who hears about reports and money problems: the admins, else the PICs (never the member it's about)."""
    return admins(session, group, besides=besides) or approvers(session, group, besides=besides)


def _notify_once(session: Session, members: list[Member], kind: str, claim: Claim, text: str, **payload) -> None:
    """Tell each member once per claim, kind (and payout, if given), however often the job runs."""
    key = {"claim_id": claim.id, **payload}
    for member in members:
        sent = session.exec(select(Notification).where(Notification.member_id == member.id,
                                                       Notification.kind == kind)).all()
        if not any(all(json.loads(n.payload_json).get(k) == v for k, v in key.items()) for n in sent):
            notify(session, member, kind, text, **key)
