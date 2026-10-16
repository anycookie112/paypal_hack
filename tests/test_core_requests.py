"""The decision engine and request actions: telling the group, approving, and every rule in docs/SPEC.md §6."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import settings
from app.core.decisions import committed_cents, decide
from app.core.members import set_paypal_email
from app.core.requests import (ActionError, NeedsInfo, approve_request, change_time, reject_request,
                               release_request, submit_request)
from app.models import FeedPost, Notification, Request
from tests.demo import NOW, item, member, past_purchase, tell

# --- the happy path and double buying -----------------------------------------------------------

def test_common_item_is_confirmed_and_the_group_sees_who_is_on_it(session, group):
    result = tell(session, "Priya", "Gloves")
    assert result.decision.outcome == "confirmed"
    assert result.request.status == "on_it" and result.request.cap_cents == 25_00
    assert result.request.on_it_until == NOW + timedelta(hours=4)
    post = session.exec(select(FeedPost)).one()
    assert (post.kind, post.text) == ("on_it", "Priya is getting Gloves")


def test_someone_already_on_it_stops_a_double_buy(session, group):
    priya = tell(session, "Priya", "Gloves").request
    result = tell(session, "Marco", "Gloves", now=NOW + timedelta(minutes=1))
    assert result.decision.outcome == "already_on_it" and result.request is None
    assert [r.id for r in result.decision.on_it] == [priya.id]
    assert len(session.exec(select(Request)).all()) == 1  # nothing recorded for Marco


def test_insisting_goes_to_an_approver_not_the_requester(session, group):
    tell(session, "Priya", "Gloves")
    result = tell(session, "Marco", "Gloves", insist=True, reason="We need two boxes")
    assert result.request.status == "needs_approval"
    assert "Someone is already getting Gloves." in result.decision.reasons
    notified = {n.member_id for n in session.exec(select(Notification)).all()}
    assert notified == {member(session, "Dana").id, member(session, "Sam").id}


def test_an_unclaimed_confirmation_expires_and_frees_the_item(session, group):
    tell(session, "Priya", "Gloves")
    later = NOW + timedelta(hours=5)
    result = tell(session, "Marco", "Gloves", now=later)
    assert result.decision.outcome == "confirmed"
    statuses = {r.member_id: r.status for r in session.exec(select(Request)).all()}
    assert statuses[member(session, "Priya").id] == "expired"


# --- the rules ------------------------------------------------------------------------------------

def test_over_the_hard_ceiling_is_declined_and_recorded(session, group):
    result = tell(session, "Priya", description="A new gazebo", estimate_cents=150_00)
    assert result.decision.outcome == "declined" and result.request.status == "declined"
    assert "normal process" in result.decision.reasons[0]


def test_uncommon_items_and_over_cap_estimates_need_approval(session, group):
    table = tell(session, "Priya", description="Folding table for the stall", estimate_cents=45_00)
    assert table.request.status == "needs_approval" and table.request.cap_cents == 45_00
    assert table.decision.reasons == ["Not on the group's list of common items."]
    pricey = tell(session, "Marco", "Gloves", estimate_cents=32_00)
    assert pricey.request.status == "needs_approval"
    assert "$32.00 is over the $25.00 limit for Gloves." in pricey.decision.reasons


def test_bought_again_too_soon_needs_approval_then_not_once_it_usually_runs_out(session, group):
    past_purchase(session, "Marco", "Gloves", 18_40, days_ago=2)
    soon = tell(session, "Priya", "Gloves")
    assert soon.request.status == "needs_approval"
    assert "Gloves was bought 2 days ago by Marco; it usually lasts 14 days." in soon.decision.reasons

    session.delete(soon.request)
    session.commit()
    later = tell(session, "Priya", "Gloves", now=NOW + timedelta(days=6))  # 8 days after: past 50% of 14
    assert later.decision.outcome == "confirmed"


def test_member_monthly_limit_counts_this_month_only(session, group):
    settings.set_value(session, group, "member_monthly_limit_cents", 40_00)
    past_purchase(session, "Priya", "Coffee", 15_00, days_ago=10)        # Oct 7: counts
    past_purchase(session, "Priya", "Tape and markers", 30_00, days_ago=20)  # Sep 27: last month
    result = tell(session, "Priya", "Snacks and water")                   # cap $30 → $45 > $40
    assert result.request.status == "needs_approval"
    assert result.decision.reasons == ["Over Priya's $40.00 monthly limit."]


def test_category_budget_and_pending_confirmations_count_as_spent(session, group):
    past_purchase(session, "Dana", "Flyer printing", 30_00, days_ago=16)  # Oct 1: this month, not too soon
    result = tell(session, "Priya", "Flyer printing")  # $30 + $25 cap > $50 printing budget
    assert result.request.status == "needs_approval"
    assert result.decision.reasons == ["Over this month's Printing budget ($50.00)."]

    tell(session, "Marco", "Gloves")  # confirmed, not bought yet: its $25 cap is promised
    assert committed_cents(session, group, NOW, member=member(session, "Marco")) == 25_00


def test_group_budget(session, group):
    group.monthly_budget_cents = 30_00
    session.add(group)
    session.commit()
    past_purchase(session, "Dana", "Coffee", 10_00, days_ago=3)
    result = tell(session, "Priya", "Gloves")
    assert "Over the group's monthly budget ($30.00)." in result.decision.reasons


# --- approvers --------------------------------------------------------------------------------------

def test_approving_confirms_posts_and_tells_the_requester(session, group):
    request = tell(session, "Priya", description="Folding table", estimate_cents=45_00).request
    approved = approve_request(session, request.id, member(session, "Dana"), cap_cents=50_00, now=NOW)
    assert approved.status == "on_it" and approved.cap_cents == 50_00
    assert approved.decided_by_id == member(session, "Dana").id
    assert session.exec(select(FeedPost)).one().text == "Priya is getting Folding table"
    told = session.exec(select(Notification).where(Notification.member_id == member(session, "Priya").id)).one()
    assert told.kind == "approved" and "up to $50.00" in told.text


def test_who_may_approve(session, group):
    own = tell(session, "Dana", description="Speaker", estimate_cents=40_00).request
    with pytest.raises(ActionError, match="your own request"):
        approve_request(session, own.id, member(session, "Dana"))
    with pytest.raises(ActionError, match="Only approvers"):
        approve_request(session, own.id, member(session, "Marco"))
    with pytest.raises(ActionError, match="limit for one purchase"):
        approve_request(session, own.id, member(session, "Sam"), cap_cents=500_00)
    approve_request(session, own.id, member(session, "Sam"))  # another approver: fine
    with pytest.raises(ActionError, match="already on it"):
        approve_request(session, own.id, member(session, "Sam"))


def test_rejecting_tells_the_requester(session, group):
    request = tell(session, "Marco", description="Bluetooth speaker", estimate_cents=60_00).request
    rejected = reject_request(session, request.id, member(session, "Sam"), note="We have one already.", now=NOW)
    assert rejected.status == "rejected"
    told = session.exec(select(Notification).where(Notification.member_id == member(session, "Marco").id)).one()
    assert told.text == "Sam didn't approve Bluetooth speaker. We have one already."


def test_bad_input(session, group):
    with pytest.raises(NeedsInfo, match="Missing: what they're getting"):
        submit_request(session, member(session, "Priya"), description="  ")
    with pytest.raises(ActionError, match="No common item"):
        submit_request(session, member(session, "Priya"), item_id=999)


# --- settings ---------------------------------------------------------------------------------------

def test_settings_defaults_overrides_and_validation(session, group):
    assert settings.get_all(session, group)["waiting_days"] == 3
    settings.set_value(session, group, "waiting_days", "5")
    settings.set_value(session, group, "feed_shows_amounts", "no")
    values = settings.get_all(session, group)
    assert values["waiting_days"] == 5 and values["feed_shows_amounts"] is False
    for key, bad in [("waiting_days", 99), ("feed_style", "hourly"), ("nope", 1), ("on_it_hours", "soon")]:
        with pytest.raises(settings.SettingError):
            settings.set_value(session, group, key, bad)
    assert settings.describe("member_monthly_limit_cents", 100_00) == "Most one member may spend per month: $100.00"


def test_decide_does_not_write_requests(session, group):
    decision = decide(session, group, member(session, "Priya"), item=item(session, "Gloves"), now=NOW)
    assert decision.outcome == "confirmed" and decision.cap_cents == 25_00
    assert session.exec(select(Request)).all() == []


# --- the member's deadline, releasing, justification ----------------------------------------------------

def test_the_member_says_when_they_will_have_it(session, group):
    request = tell(session, "Priya", "Gloves", until=NOW + timedelta(hours=2, minutes=20)).request
    assert request.on_it_until == NOW + timedelta(hours=2, minutes=20)  # exactly what they said
    with pytest.raises(ActionError, match="more than the 72 hours"):
        tell(session, "Marco", "Trash bags", until=NOW + timedelta(hours=100))
    with pytest.raises(ActionError, match="already passed"):
        tell(session, "Marco", "Trash bags", until=NOW - timedelta(minutes=5))


def test_an_approved_request_gets_the_time_the_member_asked_for(session, group):
    request = tell(session, "Priya", reason="For the stall", description="Folding table", estimate_cents=40_00,
                   until=NOW + timedelta(hours=24)).request
    approved = approve_request(session, request.id, member(session, "Dana"), now=NOW + timedelta(hours=1))
    assert approved.on_it_until == NOW + timedelta(hours=25)


def test_anything_a_pic_must_approve_needs_a_reason(session, group):
    with pytest.raises(NeedsInfo, match="Missing: why it's needed. A PIC has to approve it: Not on the group's list") \
            as missing:
        tell(session, "Priya", description="Folding table", estimate_cents=40_00, reason=" ")
    assert missing.value.missing == ["why it's needed"]
    assert session.exec(select(Request)).all() == []
    assert tell(session, "Priya", "Gloves", reason="").decision.outcome == "confirmed"  # confirmed needs none


def test_only_the_person_getting_it_can_release_it(session, group):
    request = tell(session, "Priya", "Gloves").request
    with pytest.raises(ActionError, match="Only Priya can change that request"):
        release_request(session, request.id, member(session, "Sam"))
    assert release_request(session, request.id, member(session, "Priya")).status == "released"
    assert tell(session, "Marco", "Gloves").decision.outcome == "confirmed"  # free again
    with pytest.raises(ActionError, match="already released"):
        release_request(session, request.id, member(session, "Priya"))


def test_changing_the_time_on_your_own_request(session, group):
    request = tell(session, "Priya", "Gloves").request                     # the usual 4 hours: until 12:40
    later = NOW + timedelta(minutes=20)
    eleven = NOW.replace(hour=11, minute=0)
    assert change_time(session, request.id, member(session, "Priya"), eleven, now=later).on_it_until == eleven
    with pytest.raises(ActionError, match="Only Priya can change that request"):
        change_time(session, request.id, member(session, "Marco"), eleven, now=later)
    with pytest.raises(ActionError, match="more than the 72 hours"):
        change_time(session, request.id, member(session, "Priya"), later + timedelta(hours=100), now=later)
    with pytest.raises(ActionError, match="is expired: there's no time to change"):
        change_time(session, request.id, member(session, "Priya"), eleven, now=NOW + timedelta(hours=5))


def test_paypal_email(session, group):
    priya = member(session, "Priya")
    assert set_paypal_email(session, priya, " priya@example.com ").paypal_email == "priya@example.com"
    with pytest.raises(ActionError, match="doesn't look like an email"):
        set_paypal_email(session, priya, "priya at example")
