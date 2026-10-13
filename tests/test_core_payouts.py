"""Holding and releasing payouts, against a fake PayPal (no network)."""

import asyncio
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import settings
from app.core.memory import latest_purchase
from app.core.payouts import (answer_question, approve_extra, ask_buyer, balance_cents, cancel_unclaimed,
                              record_top_up, reject_extra, report_problem, resolve_report, run_due, start_hold)
from app.core.requests import ActionError
from app.models import Claim, LedgerEntry, Member, Notification, Payout
from tests.demo import NOW, item, member, tell
from tests.fake_paypal import FakePayPal

DAY = timedelta(days=1)


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def funded(session, group):
    """The demo group with $300 in its balance and everyone's PayPal email set."""
    for m in session.exec(select(Member)).all():
        m.paypal_email = f"{m.name.lower()}@paypal.example.com"
        session.add(m)
    session.commit()
    record_top_up(session, group, 300_00, paypal_ref="TOPUP1")
    return group


def accepted(session, who="Priya", what="Gloves", total=18_40, extra=0, reason=""):
    """A confirmed request with an accepted receipt, now in its hold."""
    request = tell(session, who, what).request
    request.status = "claimed"
    claim = Claim(group_id=request.group_id, request_id=request.id, member_id=request.member_id,
                  item_id=request.item_id, total_cents=total + extra, base_cents=min(total + extra, request.cap_cents),
                  extra_cents=extra, extra_reason=reason, extra_status="pending" if extra else "none",
                  submitted_at=NOW)
    session.add_all([request, claim])
    session.commit()
    return start_hold(session, claim, NOW)


def run(session, paypal, when):
    return asyncio.run(run_due(session, paypal.client(), when))


def told(session, who, kind):
    return [n.text for n in session.exec(select(Notification).where(Notification.member_id == member(session, who).id,
                                                                    Notification.kind == kind)).all()]


# --- the hold and the payout ----------------------------------------------------------------------

def test_nothing_is_paid_during_the_hold_then_paid_after_it(session, funded, paypal):
    claim = accepted(session)
    assert claim.payout_due_at == NOW + 3 * DAY
    assert run(session, paypal, NOW + 2 * DAY).sent == [] and paypal.created == []

    result = run(session, paypal, NOW + 3 * DAY)
    payout = session.exec(select(Payout)).one()
    assert result.sent == [payout.id] and payout.sender_batch_id == f"claim-{claim.id}-base-1"
    assert paypal.created[0]["items"][0]["receiver"] == "priya@paypal.example.com"
    assert paypal.created[0]["items"][0]["amount"] == {"value": "18.40", "currency": "USD"}
    session.refresh(claim)
    assert claim.status == "paid" and payout.status == "SUCCESS" and payout.fee_cents == 53
    assert balance_cents(session, funded) == 300_00 - 18_40 - 53
    assert told(session, "Priya", "paid") == ["Paid back $18.40 for Gloves. Thanks for getting it!"]
    assert run(session, paypal, NOW + 4 * DAY).sent == []  # never paid twice


def test_pending_payouts_are_followed_up_until_paypal_settles(session, funded, paypal):
    claim = accepted(session)
    paypal.next_status = "PENDING"
    run(session, paypal, NOW + 3 * DAY)
    session.refresh(claim)
    assert claim.status == "paying" and told(session, "Priya", "paid") == []
    paypal.set_status(session.exec(select(Payout)).one().paypal_item_id, "SUCCESS")
    assert len(run(session, paypal, NOW + 3 * DAY + timedelta(minutes=5)).updated) == 1
    session.refresh(claim)
    assert claim.status == "paid"


def test_a_crash_after_recording_the_payout_never_pays_twice(session, funded, paypal):
    claim = accepted(session)
    paypal.down = True
    result = run(session, paypal, NOW + 3 * DAY)  # recorded, but PayPal didn't answer
    assert result.errors and session.exec(select(Payout)).one().status == "CREATED"
    paypal.down = False
    paypal.by_sender[f"claim-{claim.id}-base-1"] = "BATCH-EARLIER"  # PayPal did get it, after all
    paypal.batches["BATCH-EARLIER"] = "ITEM-EARLIER"
    paypal.items["ITEM-EARLIER"] = {"payout_item_id": "ITEM-EARLIER", "transaction_status": "SUCCESS",
                                    "payout_item_fee": {"currency": "USD", "value": "0.53"}}
    run(session, paypal, NOW + 3 * DAY + timedelta(minutes=1))
    assert paypal.created == []  # the retry found the earlier batch instead of paying again
    session.refresh(claim)
    assert claim.status == "paid" and session.exec(select(Payout)).one().paypal_item_id == "ITEM-EARLIER"


def test_no_paypal_email_or_low_balance_waits_and_tells_someone_once(session, group, paypal):
    claim = accepted(session)
    run(session, paypal, NOW + 3 * DAY)
    run(session, paypal, NOW + 4 * DAY)
    assert len(told(session, "Priya", "needs_paypal_email")) == 1
    priya = member(session, "Priya")
    priya.paypal_email = "priya@paypal.example.com"
    session.add(priya)
    session.commit()
    run(session, paypal, NOW + 4 * DAY)  # the group has no money yet
    assert told(session, "Sam", "needs_top_up") == ["Priya is due $18.40, but the group has $0.00. Top up to pay it."]
    record_top_up(session, group, 50_00)
    run(session, paypal, NOW + 5 * DAY)
    session.refresh(claim)
    assert claim.status == "paid"


# --- step 1: asking the person who got it (no hold) -----------------------------------------------

def test_the_ai_can_say_who_got_it(session, funded, paypal):
    claim = accepted(session)
    claim.store = "Riverside Hardware"
    session.add(claim)
    session.commit()
    details = latest_purchase(session, item(session, "Gloves"))
    assert (details.member, details.store, details.total_cents, details.status) == (
        "Priya", "Riverside Hardware", 18_40, "waiting")
    assert latest_purchase(session, item(session, "Coffee")) is None


def test_asking_the_buyer_holds_nothing_and_tells_no_treasurer(session, funded, paypal):
    claim = accepted(session)
    question = ask_buyer(session, claim.id, member(session, "Marco"), "The gloves aren't in the supply box",
                         now=NOW + DAY)
    assert "Marco is looking for the Gloves you got on" in told(session, "Priya", "question")[0]
    session.refresh(claim)
    assert claim.status == "waiting" and claim.payout_due_at == NOW + 3 * DAY  # no hold
    assert told(session, "Sam", "report") == []
    assert latest_purchase(session, item(session, "Gloves")).open_questions == 1

    with pytest.raises(ActionError, match="Only the person who bought it"):
        answer_question(session, question.id, member(session, "Dana"), "?")
    answer_question(session, question.id, member(session, "Priya"), "In the van, I'll bring them Saturday")
    assert told(session, "Marco", "question_answered") == [
        'Priya answered about the Gloves: "In the van, I\'ll bring them Saturday"']
    run(session, paypal, NOW + 3 * DAY)
    session.refresh(claim)
    assert claim.status == "paid"  # a question never delays the payout on its own


def test_a_formal_report_must_say_what_was_checked(session, funded, paypal):
    claim = accepted(session)
    with pytest.raises(ActionError, match="what you checked"):
        report_problem(session, claim.id, member(session, "Marco"), "Missing", checked=" ")
    with pytest.raises(ActionError, match="your own purchase"):
        ask_buyer(session, claim.id, member(session, "Priya"), "?")
    question = ask_buyer(session, claim.id, member(session, "Marco"), "Where are they?")
    with pytest.raises(ActionError, match="No report"):
        resolve_report(session, question.id, member(session, "Sam"), problem=True)  # a question isn't a report


# --- step 2: the formal report ------------------------------------------------------------------------

def test_a_report_holds_the_payout_privately_and_extends_the_hold(session, funded, paypal):
    claim = accepted(session)
    report_problem(session, claim.id, member(session, "Marco"), "The gloves aren't in the supply box", checked="The supply box; asked them",
                   now=NOW + 1 * DAY)
    session.refresh(claim)
    assert claim.status == "held" and claim.payout_due_at == NOW + 5 * DAY  # reported on day 1: +4 days
    assert "Marco reported a problem with Priya's Gloves" in told(session, "Sam", "report")[0]
    assert "They checked: The supply box; asked them." in told(session, "Sam", "report")[0]
    assert told(session, "Dana", "report") == []  # only the treasurer hears about reports
    assert "the treasurer will take a look" in told(session, "Priya", "claim_reported")[0]
    assert run(session, paypal, NOW + 3 * DAY).sent == []  # the original hold ended, but it's held


def test_the_treasurer_clears_a_report_and_it_is_paid(session, funded, paypal):
    claim = accepted(session)
    report = report_problem(session, claim.id, member(session, "Marco"), "Can't find them", checked="The supply box; asked them", now=NOW + DAY)
    resolve_report(session, report.id, member(session, "Sam"), problem=False, note="They were in the van.",
                   now=NOW + 2 * DAY)
    run(session, paypal, NOW + 2 * DAY)
    session.refresh(claim)
    assert claim.status == "paid" and told(session, "Priya", "claim_cleared")


def test_a_confirmed_problem_rejects_the_claim_and_goes_on_record(session, funded, paypal):
    claim = accepted(session, extra=3_00, reason="Only the big box was left")
    report = report_problem(session, claim.id, member(session, "Marco"), "Never bought", checked="The supply box; asked them", now=NOW + DAY)
    resolve_report(session, report.id, member(session, "Sam"), problem=True, now=NOW + 2 * DAY)
    session.refresh(claim)
    assert (claim.status, claim.extra_status) == ("rejected", "rejected")
    assert member(session, "Priya").problems_on_record == 1
    assert run(session, paypal, NOW + 10 * DAY).sent == []


def test_when_an_extended_hold_ends_undecided(session, funded, paypal):
    claim = accepted(session)
    report_problem(session, claim.id, member(session, "Marco"), "Hmm", checked="The supply box; asked them", now=NOW + DAY)
    run(session, paypal, NOW + 5 * DAY)  # default: paid, the report stays open for the treasurer
    session.refresh(claim)
    assert claim.status == "paid"

    settings.set_value(session, funded, "pay_after_extension", False)
    other = accepted(session, who="Marco", what="Trash bags", total=9_99)
    report_problem(session, other.id, member(session, "Priya"), "Hmm", checked="The supply box; asked them", now=NOW + DAY)
    run(session, paypal, NOW + 9 * DAY)
    session.refresh(other)
    assert other.status == "held"  # waits for the treasurer


def test_reports_after_payout_are_recorded_and_who_may_report_or_decide(session, funded, paypal):
    claim = accepted(session)
    with pytest.raises(ActionError, match="your own claim"):
        report_problem(session, claim.id, member(session, "Priya"), "?", checked="The supply box; asked them")
    run(session, paypal, NOW + 3 * DAY)
    report = report_problem(session, claim.id, member(session, "Marco"), "Never seen them", checked="The supply box; asked them", now=NOW + 4 * DAY)
    assert "already paid out" in told(session, "Sam", "report")[0]
    with pytest.raises(ActionError, match="Only the treasurer"):
        resolve_report(session, report.id, member(session, "Dana"), problem=True)
    resolve_report(session, report.id, member(session, "Sam"), problem=True)
    assert member(session, "Priya").problems_on_record == 1
    session.refresh(claim)
    assert claim.status == "paid"  # the money can't be pulled back; it's on record


# --- over budget ----------------------------------------------------------------------------------

def test_the_extra_is_paid_separately_after_the_pic_approves(session, funded, paypal):
    claim = accepted(session, total=25_00, extra=4_50, reason="Only the big box was left")
    assert claim.base_cents == 25_00
    run(session, paypal, NOW + 3 * DAY)
    session.refresh(claim)
    assert (claim.status, claim.extra_status) == ("paid", "pending") and len(paypal.created) == 1
    with pytest.raises(ActionError, match="Only a PIC"):
        approve_extra(session, claim.id, member(session, "Marco"))
    approve_extra(session, claim.id, member(session, "Dana"))
    run(session, paypal, NOW + 4 * DAY)
    session.refresh(claim)
    assert claim.extra_status == "paid" and paypal.created[1]["items"][0]["amount"]["value"] == "4.50"
    assert paypal.created[1]["sender_batch_header"]["sender_batch_id"] == f"claim-{claim.id}-extra-1"
    assert len(told(session, "Priya", "paid")) == 2


def test_a_pic_cannot_approve_their_own_extra(session, funded, paypal):
    claim = accepted(session, who="Dana", what="Snacks and water", total=30_00, extra=5_00)
    with pytest.raises(ActionError, match="your own extra"):
        approve_extra(session, claim.id, member(session, "Dana"))


def test_a_rejected_extra_still_pays_the_rest(session, funded, paypal):
    claim = accepted(session, total=25_00, extra=10_00)
    reject_extra(session, claim.id, member(session, "Dana"), note="Please check first next time.")
    run(session, paypal, NOW + 3 * DAY)
    session.refresh(claim)
    assert (claim.status, claim.extra_status) == ("paid", "rejected") and len(paypal.created) == 1


# --- unclaimed and failed payouts -----------------------------------------------------------------

def test_unclaimed_payout_can_be_cancelled_and_paid_again(session, funded, paypal):
    claim = accepted(session)
    paypal.next_status = "UNCLAIMED"
    run(session, paypal, NOW + 3 * DAY)
    session.refresh(claim)
    assert claim.status == "unclaimed" and "has no PayPal account yet" in told(session, "Priya", "payout_unclaimed")[0]
    priya = member(session, "Priya")
    priya.paypal_email = "priya.real@paypal.example.com"
    session.add(priya)
    session.commit()

    asyncio.run(cancel_unclaimed(session, paypal.client(), claim.id, member(session, "Sam"), now=NOW + 4 * DAY))
    assert balance_cents(session, funded) == 300_00 - 53  # the $18.40 came back; the first payout's fee didn't
    paypal.next_status = "SUCCESS"
    run(session, paypal, NOW + 4 * DAY)
    session.refresh(claim)
    second = session.exec(select(Payout).where(Payout.attempt == 2)).one()
    assert claim.status == "paid" and second.sender_batch_id == f"claim-{claim.id}-base-2"
    assert paypal.created[1]["items"][0]["receiver"] == "priya.real@paypal.example.com"


def test_a_failed_payout_returns_the_money_and_tells_the_treasurer(session, funded, paypal):
    claim = accepted(session)
    paypal.next_status = "PENDING"
    run(session, paypal, NOW + 3 * DAY)
    paypal.set_status(session.exec(select(Payout)).one().paypal_item_id, "FAILED")
    run(session, paypal, NOW + 3 * DAY + timedelta(hours=1))
    session.refresh(claim)
    assert claim.status == "payout_failed" and balance_cents(session, funded) == 300_00
    assert "didn't go through (FAILED)" in told(session, "Sam", "payout_failed")[0]
    kinds = [e.kind for e in session.exec(select(LedgerEntry).order_by(LedgerEntry.id)).all()]
    assert kinds == ["top_up", "payout", "payout_returned"]
