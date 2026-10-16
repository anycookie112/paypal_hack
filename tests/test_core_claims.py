"""Claims: the receipt for a confirmed request, every check in docs/SPEC.md §4.2, and on to the payout."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from app.config import UPLOADS_DIR
from app.core import settings
from app.core.claims import Upload, accept_claim, reject_claim, submit_claim
from app.core.payouts import record_top_up, run_due
from app.core.requests import ActionError, NeedsInfo
from app.models import Claim, FeedPost, Notification, Payout, Request
from tests.demo import NOW, member, tell
from tests.fake_paypal import FakePayPal

TODAY = NOW.date()


def jpg(n: int = 1) -> Upload:
    """A distinct (tiny) JPEG: the app looks at the first bytes, not the name."""
    return Upload(b"\xff\xd8\xff\xe0" + f"receipt {n}".encode(), f"receipt{n}.jpg")


def pay_to(session, *names):
    for name in names:
        m = member(session, name)
        m.paypal_email = f"{name.lower()}@paypal.example.com"
        session.add(m)
    session.commit()


def claim(session, who, request, *, receipt=None, store="Riverside Hardware", day=TODAY, total=11_99, now=NOW,
          **kwargs):
    return submit_claim(session, member(session, who), request.id, receipt=receipt or jpg(), store=store,
                        receipt_date=day, total_cents=total, now=now, **kwargs)


@pytest.fixture
def ready(session, group):
    """Priya and Marco can be paid; Priya is getting trash bags (a receipt is enough for those)."""
    pay_to(session, "Priya", "Marco")
    return tell(session, "Priya", "Trash bags").request


# --- the happy path -------------------------------------------------------------------------------

def test_a_receipt_within_the_limit_starts_the_hold_and_the_group_sees_it(session, ready):
    result = claim(session, "Priya", ready, total=11_99)
    c = result.claim
    assert (result.outcome, c.status, c.base_cents, c.extra_cents) == ("accepted", "waiting", 11_99, 0)
    assert c.payout_due_at == NOW + timedelta(days=3)
    assert session.get(Request, ready.id).status == "claimed"
    got = session.exec(select(FeedPost).where(FeedPost.kind == "got_it")).one()
    assert got.text == "Priya got Trash bags, $11.99"
    assert (UPLOADS_DIR / c.receipt_path).read_bytes() == jpg().data


def test_the_feed_leaves_out_amounts_if_the_group_does(session, ready, group):
    settings.set_value(session, group, "feed_shows_amounts", False)
    claim(session, "Priya", ready)
    assert session.exec(select(FeedPost).where(FeedPost.kind == "got_it")).one().text == "Priya got Trash bags"


def test_from_request_to_paypal_after_the_hold(session, ready, group):
    record_top_up(session, group, 300_00, paypal_ref="TOPUP1")
    claim(session, "Priya", ready, total=11_99)
    paypal = FakePayPal()
    assert asyncio.run(run_due(session, paypal.client(), NOW + timedelta(days=2))).sent == []   # still in its hold
    asyncio.run(run_due(session, paypal.client(), NOW + timedelta(days=3)))
    payout = session.exec(select(Payout)).one()
    assert (payout.amount_cents, payout.status) == (11_99, "SUCCESS")
    assert session.exec(select(Claim)).one().status == "paid"


# --- what has to be there -------------------------------------------------------------------------

def test_whats_missing_is_listed_by_code(session, group):
    gloves = tell(session, "Priya", "Gloves").request  # gloves need a photo of where they're kept
    with pytest.raises(NeedsInfo) as missing:
        submit_claim(session, member(session, "Priya"), gloves.id, receipt=jpg(), store=" ", receipt_date=None,
                     total_cents=18_40, now=NOW)
    assert missing.value.missing == ["the store", "the date on the receipt", "a photo of where it's kept",
                                     "their PayPal email"]
    assert session.exec(select(Claim)).all() == []


def test_only_your_own_confirmed_request_can_be_claimed(session, ready):
    with pytest.raises(ActionError, match="That's Priya's request: only they can claim it"):
        claim(session, "Marco", ready)
    table = tell(session, "Marco", description="Folding table", estimate_cents=40_00).request
    with pytest.raises(ActionError, match="needs approval: there's nothing to claim"):
        claim(session, "Marco", table)
    claim(session, "Priya", ready)
    with pytest.raises(ActionError, match="already has a receipt"):
        claim(session, "Priya", ready, receipt=jpg(2))


@pytest.mark.parametrize("upload, error", [
    (Upload(b"hello, I am a text file", "receipt.jpg"), "isn't a photo or a PDF"),
    (Upload(b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024), "huge.jpg"), "over 10 MB"),
])
def test_only_photos_and_pdfs_up_to_10_mb(session, ready, upload, error):
    with pytest.raises(ActionError, match=error):
        claim(session, "Priya", ready, receipt=upload)


def test_phone_photo_formats_are_accepted(session, group):
    pay_to(session, "Priya", "Marco")
    heic = Upload(b"\x00\x00\x00\x18ftypheic" + b"photo", "IMG_0001.HEIC")
    pdf = Upload(b"%PDF-1.7 receipt", "receipt.pdf")
    for who, what, upload, total in (("Priya", "Trash bags", heic, 10_00), ("Marco", "Coffee", pdf, 12_00)):
        result = claim(session, who, tell(session, who, what).request, receipt=upload, total=total)
        assert result.outcome == "accepted"
    assert sorted(Path(c.receipt_path).suffix for c in session.exec(select(Claim))) == [".heic", ".pdf"]


# --- checks that send it to a PIC -----------------------------------------------------------------

def test_the_same_receipt_twice_is_refused(session, ready):
    claim(session, "Priya", ready, receipt=jpg(1))
    coffee = tell(session, "Priya", "Coffee").request
    with pytest.raises(ActionError, match=r"already handed in \(claim 1\)"):
        claim(session, "Priya", coffee, receipt=jpg(1))


def test_same_store_date_and_total_goes_to_a_pic(session, ready):
    claim(session, "Priya", ready, total=11_99)
    coffee = tell(session, "Marco", "Coffee").request
    result = claim(session, "Marco", coffee, receipt=jpg(2), total=11_99)
    assert (result.outcome, result.claim.status) == ("review", "review")
    assert result.reasons == ["Another receipt has the same store, date and total (claim 1, Priya)."]
    told = {n.member_id for n in session.exec(select(Notification).where(Notification.kind == "claim_review"))}
    assert told == {member(session, "Dana").id, member(session, "Sam").id}
    assert result.claim.payout_due_at is None  # no hold until a PIC says it's fine


@pytest.mark.parametrize("day, reason", [
    (TODAY - timedelta(days=2), "The receipt is dated Thu 15 Oct, before it was confirmed (Sat 17 Oct)."),
    (TODAY + timedelta(days=1), "The receipt is dated Sun 18 Oct, which hasn't happened yet."),
])
def test_a_receipt_dated_wrong_goes_to_a_pic(session, ready, day, reason):
    assert claim(session, "Priya", ready, day=day).reasons == [reason]


def test_a_receipt_after_the_time_ran_out_goes_to_a_pic(session, ready):
    later = NOW + timedelta(hours=5)                  # trash bags were Priya's until 12:40
    marco = tell(session, "Marco", "Trash bags", now=later).request
    claim(session, "Marco", marco, receipt=jpg(2), total=9_50, now=later)
    result = claim(session, "Priya", ready, now=later + timedelta(minutes=5))
    assert result.reasons == ["Handed in after the time to get it ran out (Sat 17 Oct, 12:40).",
                              "Marco has handed in a receipt for Trash bags since then."]


# --- over the limit -------------------------------------------------------------------------------

def test_over_the_limit_needs_a_reason_then_the_extra_waits_for_a_pic(session, ready):
    with pytest.raises(NeedsInfo, match=r"why it cost more than the \$15.00 limit.*a PIC decides on the other \$3.00"):
        claim(session, "Priya", ready, total=18_00)
    result = claim(session, "Priya", ready, total=18_00, extra_reason="Only the big pack was left")
    c = result.claim
    assert (result.outcome, c.base_cents, c.extra_cents, c.extra_status) == ("accepted", 15_00, 3_00, "pending")
    pending = session.exec(select(Notification).where(Notification.kind == "extra_pending")).all()
    assert {n.member_id for n in pending} == {member(session, "Dana").id, member(session, "Sam").id}
    assert '"Only the big pack was left"' in pending[0].text


def test_never_more_than_the_groups_limit_for_one_purchase(session, ready):
    result = claim(session, "Priya", ready, total=140_00, extra_reason="Bought for the whole season")
    assert (result.claim.base_cents, result.claim.extra_cents) == (15_00, 85_00)  # up to $100 in all
    assert result.reasons == ["The total is over the group's $100.00 limit for one purchase: at most that is paid back."]


# --- a PIC decides ----------------------------------------------------------------------------------

def test_a_pic_accepts_a_flagged_claim_and_the_hold_starts_then(session, ready):
    flagged = claim(session, "Priya", ready, day=TODAY + timedelta(days=1)).claim
    with pytest.raises(ActionError, match="Only PICs"):
        accept_claim(session, flagged.id, member(session, "Marco"))
    accepted = accept_claim(session, flagged.id, member(session, "Dana"), now=NOW + timedelta(hours=2))
    assert (accepted.status, accepted.payout_due_at) == ("waiting", NOW + timedelta(hours=2, days=3))
    assert session.exec(select(FeedPost).where(FeedPost.kind == "got_it")).one().text == "Priya got Trash bags, $11.99"
    with pytest.raises(ActionError, match="is waiting: there's nothing to decide"):
        reject_claim(session, flagged.id, member(session, "Sam"))


def test_a_pic_rejects_a_flagged_claim_and_nothing_is_paid(session, group):
    pay_to(session, "Dana")
    request = tell(session, "Dana", "Trash bags").request
    flagged = claim(session, "Dana", request, day=TODAY - timedelta(days=3)).claim
    with pytest.raises(ActionError, match="can't decide on your own receipt"):
        reject_claim(session, flagged.id, member(session, "Dana"))
    rejected = reject_claim(session, flagged.id, member(session, "Sam"), note="That's an old receipt.")
    assert rejected.status == "rejected"
    told = session.exec(select(Notification).where(Notification.kind == "claim_rejected")).one()
    assert told.text == "Sam couldn't accept your receipt for Trash bags. That's an old receipt."
