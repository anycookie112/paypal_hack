"""Handing in receipts, and PICs deciding on the ones a check flagged. The checks are in app/core/claims.py."""

import hashlib
from datetime import date, datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from app.ai.model import AgentError
from app.ai.receipts import read_receipt
from app.api.deps import MemberDep, SessionDep
from app.config import UPLOADS_DIR
from app.core import claims, memory, receipts
from app.core.receipts import Reading
from app.core.requests import ActionError, thing
from app.domain.money import to_cents
from app.models import Claim, Group, Member, Request

router = APIRouter()


class ClaimOut(BaseModel):
    id: int
    item: str
    member: str
    status: str
    store: str
    receipt_date: date | None
    total_cents: int
    base_cents: int
    extra_cents: int
    extra_reason: str
    extra_status: str
    reasons: list[str]
    payout_due_at: datetime | None
    receipt_url: str
    photo_url: str


class SubmittedOut(BaseModel):
    outcome: str  # accepted | review
    claim: ClaimOut


class RejectIn(BaseModel):
    note: str = ""


@router.get("/claimable", response_model=list[claims.Claimable])
def claimable(session: SessionDep, member: MemberDep):
    return claims.claimable(session, member)


class ReadOut(BaseModel):
    reading: Reading | None   # None: the reader didn't run or failed; the member types the values
    note: str = ""            # why there's no reading
    currency: str             # the group's: a receipt in another currency goes to a PIC


@router.post("/claims/read", response_model=ReadOut)
async def read(session: SessionDep, member: MemberDep, request_id: int = Form(), receipt: UploadFile = File()):
    """Read a receipt before it's handed in, to fill in the claim form. The reading is kept by the file's hash, so
    handing in the same file compares it with what the member entered (app/core/receipts.py)."""
    request = session.get(Request, request_id)
    if request is None or request.member_id != member.id:
        raise ActionError(f"No request of yours with id {request_id}.")
    upload = await _upload(receipt)
    if upload is None:
        raise ActionError("Choose the receipt first.")
    kind = claims.file_kind(upload)
    currency = session.get(Group, member.group_id).currency
    try:
        reading, call = await read_receipt(upload.data, kind, what=thing(session, request))
    except AgentError as exc:
        memory.log_step(session, agent="receipt", event="error", name="read", data={"reason": str(exc)})
        return ReadOut(reading=None, note="Couldn't read it just now: fill in the store, date and total.",
                       currency=currency)
    receipts.save_reading(session, member, request.id, hashlib.sha256(upload.data).hexdigest(), reading,
                          call["prompt_id"])
    memory.log_step(session, agent="receipt", event="output", name="read", prompt_id=call["prompt_id"],
                    data={**call, "reading": reading.model_dump()})
    return ReadOut(reading=reading, currency=currency)


@router.post("/claims", response_model=SubmittedOut)
async def submit(session: SessionDep, member: MemberDep, request_id: int = Form(), store: str = Form(""),
                 receipt_date: date | None = Form(None), total: str = Form(""), extra_reason: str = Form(""),
                 receipt: UploadFile | None = File(None), photo: UploadFile | None = File(None)):
    try:
        total_cents = to_cents(total) if total.strip() else None
    except ValueError as exc:
        raise ActionError(f"The total: {exc}.")
    result = claims.submit_claim(session, member, request_id, receipt=await _upload(receipt), store=store,
                                 receipt_date=receipt_date, total_cents=total_cents, photo=await _upload(photo),
                                 extra_reason=extra_reason)
    return SubmittedOut(outcome=result.outcome, claim=_out(session, result.claim))


@router.get("/claims/review", response_model=list[ClaimOut])
def for_review(session: SessionDep, member: MemberDep):
    """Receipts a check flagged, for the PICs (everyone else gets an empty list)."""
    if not member.can_approve:
        return []
    flagged = session.exec(select(Claim).where(Claim.group_id == member.group_id, Claim.status == "review")
                           .order_by(Claim.submitted_at)).all()
    return [_out(session, c) for c in flagged]


@router.post("/claims/{claim_id}/accept", response_model=ClaimOut)
def accept(claim_id: int, session: SessionDep, member: MemberDep):
    return _out(session, claims.accept_claim(session, claim_id, member))


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
def reject(claim_id: int, body: RejectIn, session: SessionDep, member: MemberDep):
    return _out(session, claims.reject_claim(session, claim_id, member, note=body.note))


@router.get("/uploads/{name}", include_in_schema=False)
def upload_file(name: str, session: SessionDep, member: MemberDep):
    """A receipt or item photo: for the member who handed it in, and the PICs."""
    claim = session.exec(select(Claim).where((Claim.receipt_path == name) | (Claim.photo_path == name),
                                             Claim.group_id == member.group_id)).first()
    if claim is None or (claim.member_id != member.id and not member.can_approve):
        raise HTTPException(404)
    return FileResponse(UPLOADS_DIR / name)


async def _upload(file: UploadFile | None) -> claims.Upload | None:
    if file is None or not file.filename:
        return None
    return claims.Upload(await file.read(), file.filename)


def _out(session, claim: Claim) -> ClaimOut:
    return ClaimOut(
        id=claim.id, item=thing(session, session.get(Request, claim.request_id)),
        member=session.get(Member, claim.member_id).name, status=claim.status, store=claim.store,
        receipt_date=claim.receipt_date, total_cents=claim.total_cents, base_cents=claim.base_cents,
        extra_cents=claim.extra_cents, extra_reason=claim.extra_reason, extra_status=claim.extra_status,
        reasons=claim.reasons, payout_due_at=claim.payout_due_at, receipt_url=f"/api/uploads/{claim.receipt_path}" if claim.receipt_path else "",
        photo_url=f"/api/uploads/{claim.photo_path}" if claim.photo_path else "")
