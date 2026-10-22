"""What the PICs decide on, besides flagged receipts (app/api/claims.py): requests waiting for a PIC, and extras paid
over a request's limit. The rules (who may decide, the hard ceiling) are in app/core."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import MemberDep, SessionDep
from app.core import payouts
from app.core.requests import ActionError, approve_request, reject_request, thing
from app.domain.money import range_to_str, to_cents
from app.models import Claim, Member, Request

router = APIRouter()


class WaitingRequest(BaseModel):
    id: int
    member: str
    item: str
    quantity: int
    estimate_cents: int | None
    price: str | None        # the member's price range in words ("$30.00–$50.00")
    cap_cents: int           # what the rules suggest paying back up to; the PIC can change it
    reason: str              # the member's own words
    reasons: list[str]       # why the rules sent it to a PIC
    hours: int | None        # how long they said they need once it's approved; None = the group's usual time
    created_at: datetime


class WaitingExtra(BaseModel):
    claim_id: int
    member: str
    item: str
    total_cents: int
    base_cents: int          # paid after the hold anyway
    extra_cents: int         # only if a PIC approves
    extra_reason: str
    receipt_url: str


class Approvals(BaseModel):
    requests: list[WaitingRequest]
    extras: list[WaitingExtra]


class ApproveIn(BaseModel):
    cap: str = ""  # dollars; empty = what the rules suggest


class RejectIn(BaseModel):
    note: str = ""


@router.get("/approvals", response_model=Approvals)
def waiting(session: SessionDep, member: MemberDep):
    """Everything waiting for a PIC's yes or no, oldest first (everyone else gets empty lists)."""
    if not member.can_approve:
        return Approvals(requests=[], extras=[])
    requests = session.exec(select(Request).where(Request.group_id == member.group_id,
                                                  Request.status == "needs_approval")
                            .order_by(Request.created_at)).all()
    extras = session.exec(select(Claim).where(Claim.group_id == member.group_id, Claim.extra_status == "pending",
                                              Claim.status != "rejected").order_by(Claim.submitted_at)).all()
    return Approvals(requests=[_request(session, r) for r in requests], extras=[_extra(session, c) for c in extras])


@router.post("/requests/{request_id}/approve", response_model=WaitingRequest)
def approve(request_id: int, body: ApproveIn, session: SessionDep, member: MemberDep):
    try:
        cap = to_cents(body.cap) if body.cap.strip() else None
    except ValueError as exc:
        raise ActionError(f"The most paid back: {exc}.")
    return _request(session, approve_request(session, request_id, member, cap_cents=cap))


@router.post("/requests/{request_id}/reject", response_model=WaitingRequest)
def reject(request_id: int, body: RejectIn, session: SessionDep, member: MemberDep):
    return _request(session, reject_request(session, request_id, member, note=body.note))


@router.post("/claims/{claim_id}/extra/approve", response_model=WaitingExtra)
def approve_extra(claim_id: int, session: SessionDep, member: MemberDep):
    return _extra(session, payouts.approve_extra(session, claim_id, member))


@router.post("/claims/{claim_id}/extra/reject", response_model=WaitingExtra)
def reject_extra(claim_id: int, body: RejectIn, session: SessionDep, member: MemberDep):
    return _extra(session, payouts.reject_extra(session, claim_id, member, note=body.note))


def _request(session, request: Request) -> WaitingRequest:
    return WaitingRequest(id=request.id, member=session.get(Member, request.member_id).name,
                          item=thing(session, request), quantity=request.quantity,
                          estimate_cents=request.estimate_cents,
                          price=range_to_str(request.estimate_low_cents, request.estimate_cents)
                          if request.estimate_cents else None,
                          cap_cents=request.cap_cents, reason=request.reason,
                          reasons=request.reasons, hours=request.hours, created_at=request.created_at)


def _extra(session, claim: Claim) -> WaitingExtra:
    return WaitingExtra(claim_id=claim.id, member=session.get(Member, claim.member_id).name,
                        item=thing(session, session.get(Request, claim.request_id)), total_cents=claim.total_cents,
                        base_cents=claim.base_cents, extra_cents=claim.extra_cents, extra_reason=claim.extra_reason,
                        receipt_url=f"/api/uploads/{claim.receipt_path}" if claim.receipt_path else "")
