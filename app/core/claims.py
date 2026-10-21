"""Claims: the receipt for a confirmed request, checked by code, then held and paid back (docs/SPEC.md §4.2, §6).

    claimable      the member's confirmed requests without a receipt yet
    submit_claim   the member hands in the receipt (+ a photo of where it's kept, if the item asks for one)
    accept_claim   a PIC looked at a claim a check flagged, and it's fine: the hold starts
    reject_claim   ... or it isn't: nothing is paid

Checks, in order (alerts aren't accusations: anything unusual goes to a PIC, nothing is refused on suspicion):

    linked     their own request, confirmed            expired, or someone else got it since → a PIC looks
    dated      on or after it was confirmed, not later than today                                → a PIC looks
    duplicate  the same file as another claim → refused; the same store + date + total          → a PIC looks
    budget     total up to the item's limit; over it, the limit part goes ahead and the extra waits for a PIC
               with the member's reason (never above the group's limit for one purchase)
    payable    they have a PayPal email; the photo is there if the item asks for one             → missing

Until the receipt reader exists, the member types the store, date and total.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, select

from app.config import UPLOADS_DIR
from app.core import settings
from app.core.decisions import ACCEPTED_CLAIMS, expire_stale
from app.core.payouts import start_hold
from app.core.requests import ActionError, NeedsInfo, approvers, notify, thing
from app.core.states import move
from app.domain.money import cents_to_str
from app.models import Claim, FeedPost, Group, Item, Member, Request, utcnow

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class Upload:
    """A file from a browser upload or a chat download: just bytes (docs/SPEC.md §14, rule 5)."""
    data: bytes
    filename: str = ""


@dataclass
class SubmittedClaim:
    claim: Claim
    outcome: Literal["accepted", "review"]
    reasons: list[str] = field(default_factory=list)  # review: why a PIC has to look at it


@dataclass
class Claimable:
    """A request the member can hand a receipt in for."""
    request_id: int
    item: str
    cap_cents: int          # the most paid back without a PIC
    status: str             # on_it | expired (still claimable: a PIC looks at it)
    until: datetime | None
    needs_photo: bool       # the item asks for a photo of where it's kept


def claimable(session: Session, member: Member, now: datetime | None = None, *, days: int = 14) -> list[Claimable]:
    """The member's confirmed requests without a receipt yet, newest first; expired ones from the last `days` too
    (people sometimes hand the receipt in late)."""
    now = now or utcnow()
    expire_stale(session, member.group_id, now)
    since = now - timedelta(days=days)
    requests = session.exec(select(Request).where(Request.member_id == member.id,
                                                  Request.status.in_(("on_it", "expired")),
                                                  Request.created_at >= since)
                            .order_by(Request.created_at.desc())).all()
    out = []
    for r in requests:
        item = session.get(Item, r.item_id) if r.item_id else None
        out.append(Claimable(r.id, thing(session, r), r.cap_cents, r.status, r.on_it_until,
                             bool(item and item.proof == "receipt_photo")))
    return out


def submit_claim(session: Session, by: Member, request_id: int, *, receipt: Upload | None, store: str,
                 receipt_date: date | None, total_cents: int | None, photo: Upload | None = None,
                 extra_reason: str = "", now: datetime | None = None) -> SubmittedClaim:
    now = now or utcnow()
    request = session.get(Request, request_id)
    if request is None or request.group_id != by.group_id:
        raise ActionError(f"No request with id {request_id}.")
    if request.member_id != by.id:
        raise ActionError(f"That's {session.get(Member, request.member_id).name}'s request: only they can claim it.")
    expire_stale(session, request.group_id, now)  # past its time but not marked yet
    session.refresh(request)
    if request.status == "claimed":
        raise ActionError("This request already has a receipt.")
    if request.status not in ("on_it", "expired"):
        raise ActionError(f"This request is {request.status.replace('_', ' ')}: there's nothing to claim.")
    group = session.get(Group, request.group_id)
    item = session.get(Item, request.item_id) if request.item_id else None
    config = settings.get_all(session, group)

    missing = [what for what, absent in (
        ("the receipt", receipt is None or not receipt.data),
        ("the store", not store.strip()),
        ("the date on the receipt", receipt_date is None),
        ("the total", not total_cents),
        ("a photo of where it's kept", item is not None and item.proof == "receipt_photo" and not photo),
        ("their PayPal email", not by.paypal_email),
    ) if absent]
    if missing:
        raise NeedsInfo(missing)
    if total_cents < 0:
        raise ActionError("The total can't be negative.")
    receipt_kind, photo_kind = _kind(receipt), _kind(photo) if photo else None
    receipt_hash = hashlib.sha256(receipt.data).hexdigest()
    if same := session.exec(select(Claim).where(Claim.receipt_hash == receipt_hash,
                                                Claim.status != "rejected")).first():
        raise ActionError(f"This receipt was already handed in (claim {same.id}): use the receipt for this purchase.")

    ceiling = config["hard_ceiling_cents"]
    base = min(total_cents, request.cap_cents)
    extra = max(0, min(total_cents, ceiling) - request.cap_cents)
    if extra and not extra_reason.strip():
        raise NeedsInfo([f"why it cost more than the {cents_to_str(request.cap_cents)} limit"],
                        f"The {cents_to_str(request.cap_cents)} is paid back after the hold; a PIC decides on the "
                        f"other {cents_to_str(extra)}.")

    reasons = []
    if request.status == "expired":
        reasons.append(f"Handed in after the time to get it ran out ({request.on_it_until:%a %d %b, %H:%M}).")
        if request.item_id and (other := session.exec(
                select(Claim).where(Claim.item_id == request.item_id, Claim.status.in_(ACCEPTED_CLAIMS),
                                    Claim.submitted_at > request.created_at)).first()):
            reasons.append(f"{session.get(Member, other.member_id).name} has handed in a receipt for "
                           f"{thing(session, request)} since then.")
    confirmed = (request.decided_at or request.created_at).date()
    if receipt_date < confirmed:
        reasons.append(f"The receipt is dated {receipt_date:%a %d %b}, before it was confirmed ({confirmed:%a %d %b}).")
    elif receipt_date > now.date():
        reasons.append(f"The receipt is dated {receipt_date:%a %d %b}, which hasn't happened yet.")
    if same := session.exec(select(Claim).where(Claim.group_id == group.id, Claim.store == store.strip(),
                                                Claim.receipt_date == receipt_date,
                                                Claim.total_cents == total_cents,
                                                Claim.status.in_(ACCEPTED_CLAIMS))).first():
        reasons.append(f"Another receipt has the same store, date and total (claim {same.id}, "
                       f"{session.get(Member, same.member_id).name}).")
    if total_cents > ceiling:
        reasons.append(f"The total is over the group's {cents_to_str(ceiling)} limit for one purchase: at most that "
                       "is paid back.")

    claim = Claim(group_id=group.id, request_id=request.id, member_id=by.id, item_id=request.item_id,
                  receipt_path=_save(receipt, receipt_hash, receipt_kind),
                  receipt_hash=receipt_hash, store=store.strip(), receipt_date=receipt_date, total_cents=total_cents,
                  base_cents=base, extra_cents=extra, extra_reason=extra_reason.strip() if extra else "",
                  extra_status="pending" if extra else "none", reasons_json=json.dumps(reasons), submitted_at=now,
                  status="")
    if photo:
        claim.photo_path = _save(photo, hashlib.sha256(photo.data).hexdigest(), photo_kind)
    move(session, request, "claimed", by=by, now=now)
    session.add(claim)
    session.flush()

    what = thing(session, request)
    if reasons:
        move(session, claim, "review", by=by, now=now)
        for pic in approvers(session, group, besides=by):
            notify(session, pic, "claim_review", f"{by.name}'s receipt for {what} ({cents_to_str(total_cents)}) "
                   f"needs a look: {' '.join(reasons)}", claim_id=claim.id)
        session.commit()
        session.refresh(claim)
        return SubmittedClaim(claim, "review", reasons)

    _accepted(session, claim, by, what, config, now)
    return SubmittedClaim(session.get(Claim, claim.id), "accepted")


def accept_claim(session: Session, claim_id: int, by: Member, *, now: datetime | None = None) -> Claim:
    """A PIC looked at a flagged claim and it's fine: the hold starts now."""
    now = now or utcnow()
    claim = _in_review(session, claim_id, by)
    buyer = session.get(Member, claim.member_id)
    what = thing(session, session.get(Request, claim.request_id))
    notify(session, buyer, "claim_accepted", f"{by.name} checked your receipt for {what}: it'll be paid back after "
           "the hold.", claim_id=claim.id)
    _accepted(session, claim, buyer, what, settings.get_all(session, session.get(Group, claim.group_id)), now)
    return session.get(Claim, claim.id)


def reject_claim(session: Session, claim_id: int, by: Member, *, note: str = "") -> Claim:
    """A PIC looked at a flagged claim and it can't be paid back."""
    claim = _in_review(session, claim_id, by)
    move(session, claim, "rejected", by=by)
    if claim.extra_status == "pending":
        claim.extra_status = "rejected"
    session.add(claim)
    what = thing(session, session.get(Request, claim.request_id))
    notify(session, session.get(Member, claim.member_id), "claim_rejected",
           f"{by.name} couldn't accept your receipt for {what}." + (f" {note.strip()}" if note.strip() else ""),
           claim_id=claim.id)
    session.commit()
    session.refresh(claim)
    return claim


# --- helpers --------------------------------------------------------------------------------------

def _accepted(session: Session, claim: Claim, buyer: Member, what: str, config: dict, now: datetime) -> None:
    """The group sees it was bought, a PIC hears about any extra, and the hold starts."""
    amount = f", {cents_to_str(claim.total_cents)}" if config["feed_shows_amounts"] else ""
    session.add(FeedPost(group_id=claim.group_id, request_id=claim.request_id, member_id=buyer.id, kind="got_it",
                         text=f"{buyer.name} got {what}{amount}"))
    if claim.extra_status == "pending":
        for pic in approvers(session, session.get(Group, claim.group_id), besides=buyer):
            notify(session, pic, "extra_pending", f"{buyer.name} paid {cents_to_str(claim.extra_cents)} over the limit "
                   f"for {what}: \"{claim.extra_reason}\"", claim_id=claim.id)
    start_hold(session, claim, now)  # commits


def _in_review(session: Session, claim_id: int, by: Member) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None or claim.group_id != by.group_id:
        raise ActionError(f"No claim with id {claim_id}.")
    if not by.can_approve:
        raise ActionError("Only PICs can decide on receipts.")
    if claim.member_id == by.id:
        raise ActionError("You can't decide on your own receipt: ask another PIC.")
    if claim.status != "review":
        raise ActionError(f"This claim is {claim.status}: there's nothing to decide.")
    return claim


_KINDS = [(b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"%PDF", ".pdf")]


def _kind(upload: Upload) -> str:
    """The file's real type from its first bytes (not its name): a photo or a PDF, up to 10 MB."""
    if len(upload.data) > MAX_UPLOAD_BYTES:
        raise ActionError(f"\"{upload.filename or 'That file'}\" is over 10 MB: send a smaller photo.")
    head = upload.data[:16]
    for magic, extension in _KINDS:
        if head.startswith(magic):
            return extension
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return ".heic"
    raise ActionError(f"\"{upload.filename or 'That file'}\" isn't a photo or a PDF: send a JPG, PNG, HEIC, WEBP "
                      "or PDF.")


def _save(upload: Upload, digest: str, extension: str) -> str:
    """Saved once under its hash (the same file twice is one file). Returns the path under UPLOADS_DIR."""
    name = f"{digest}{extension}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOADS_DIR / name
    if not path.exists():
        path.write_bytes(upload.data)
    return name
