"""The approvals API: requests waiting for a PIC and extras over a limit, approved or rejected by a PIC."""

import pytest
from sqlmodel import Session, select

from app.core.claims import Upload, submit_claim
from app.core.requests import submit_request
from app.db import engine
from app.models import Member, Notification, Request
from tests.demo import NOW, item, make_group, member


@pytest.fixture
def people(client):
    with Session(engine) as session:
        make_group(session)
        for m in session.exec(select(Member)).all():
            m.paypal_email = f"{m.name.lower()}@paypal.example.com"
            session.add(m)
        session.commit()
        return {name: {"X-Member-Id": str(member(session, name).id)} for name in ("Priya", "Marco", "Dana", "Sam")}


def table(who="Marco"):
    """Something not on the list, with a reason: it waits for a PIC."""
    with Session(engine) as session:
        return submit_request(session, member(session, who), description="Folding table", estimate_cents=40_00,
                              reason="To show the flyers at the market").request.id


def test_a_pic_sees_what_waits_for_them(client, people):
    request_id = table()
    assert client.get("/api/approvals", headers=people["Priya"]).json() == {"requests": [], "extras": []}
    [waiting] = client.get("/api/approvals", headers=people["Dana"]).json()["requests"]
    assert (waiting["id"], waiting["member"], waiting["item"], waiting["estimate_cents"], waiting["cap_cents"],
            waiting["reason"]) == (request_id, "Marco", "Folding table", 40_00, 40_00,
                                   "To show the flyers at the market")
    assert waiting["reasons"] == ["Not on the group's list of common items."]


def test_approving_with_a_different_cap(client, people):
    request_id = table()
    approved = client.post(f"/api/requests/{request_id}/approve", json={"cap": "35"}, headers=people["Dana"])
    assert approved.status_code == 200 and approved.json()["cap_cents"] == 35_00
    with Session(engine) as session:
        request = session.get(Request, request_id)
        assert request.status == "on_it"
        told = session.exec(select(Notification).where(Notification.kind == "approved")).one()
        assert told.text == "Dana approved Folding table, up to $35.00. Go ahead!"
    assert client.get("/api/approvals", headers=people["Dana"]).json()["requests"] == []


def test_rejecting_with_a_note(client, people):
    request_id = table()
    rejected = client.post(f"/api/requests/{request_id}/reject", json={"note": "We have one in storage."},
                           headers=people["Sam"])
    assert rejected.status_code == 200
    with Session(engine) as session:
        assert session.get(Request, request_id).status == "rejected"
        told = session.exec(select(Notification).where(Notification.kind == "rejected")).one()
        assert told.text == "Sam didn't approve Folding table. We have one in storage."


def test_who_may_decide(client, people):
    request_id = table("Dana")  # a PIC's own request: another PIC decides
    own = client.post(f"/api/requests/{request_id}/approve", json={}, headers=people["Dana"])
    assert own.status_code == 400 and "another approver" in own.json()["detail"]
    member_says = client.post(f"/api/requests/{request_id}/approve", json={}, headers=people["Priya"])
    assert member_says.status_code == 400 and "Only approvers" in member_says.json()["detail"]
    too_much = client.post(f"/api/requests/{request_id}/approve", json={"cap": "500"}, headers=people["Sam"])
    assert too_much.status_code == 400 and "limit for one purchase" in too_much.json()["detail"]
    bad = client.post(f"/api/requests/{request_id}/approve", json={"cap": "lots"}, headers=people["Sam"])
    assert bad.status_code == 400 and bad.json()["detail"].startswith("The most paid back:")


def test_an_extra_over_the_limit(client, people):
    with Session(engine) as session:
        bags = item(session, "Trash bags")
        request = submit_request(session, member(session, "Priya"), item_id=bags.id).request
        claim = submit_claim(session, member(session, "Priya"), request.id,
                             receipt=Upload(b"\xff\xd8\xff\xe0 r", "r.jpg"), store="Riverside Hardware", receipt_date=NOW.date(), total_cents=bags.cap_cents + 5_00,
                             extra_reason="Only the big pack was left").claim
        claim_id, cap = claim.id, bags.cap_cents
    [extra] = client.get("/api/approvals", headers=people["Dana"]).json()["extras"]
    assert (extra["claim_id"], extra["member"], extra["base_cents"], extra["extra_cents"], extra["extra_reason"]) == (
        claim_id, "Priya", cap, 5_00, "Only the big pack was left")
    assert client.post(f"/api/claims/{claim_id}/extra/approve", headers=people["Dana"]).status_code == 200
    assert client.get("/api/approvals", headers=people["Dana"]).json()["extras"] == []
