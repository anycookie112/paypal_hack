"""The home page, the feed (👍 and "something's wrong?") and the chat, through the API."""

import json

import pytest
from sqlmodel import Session, select

from app.ai import main as ai_main
from app.ai.agents import ActionAgent, LookupAgent
from app.core.claims import Upload, submit_claim
from app.core.requests import submit_request
from app.db import engine
from app.models import Member
from tests.demo import item, make_group, member
from tests.fakes import FakeChatModel, call


@pytest.fixture
def people(client):
    with Session(engine) as session:
        make_group(session)
        for m in session.exec(select(Member)).all():
            m.paypal_email = f"{m.name.lower()}@paypal.example.com"
            session.add(m)
        session.commit()
        return {name: {"X-Member-Id": str(member(session, name).id)} for name in ("Priya", "Marco", "Dana", "Sam")}


def bought(who="Priya", what="Trash bags", total=11_99):
    """`who` got `what` and handed in the receipt: an on-it post, then a got-it post."""
    from datetime import datetime, timezone
    with Session(engine) as session:
        buyer = member(session, who)
        request = submit_request(session, buyer, item_id=item(session, what).id).request
        claim = submit_claim(session, buyer, request.id, receipt=Upload(b"\xff\xd8\xff" + what.encode()),
                             store="Riverside Hardware", receipt_date=datetime.now(timezone.utc).date(),
                             total_cents=total).claim
        return request.id, claim.id


# --- home -------------------------------------------------------------------------------------------

def test_home_shows_my_month_my_requests_and_the_groups_budgets(client, people):
    with Session(engine) as session:
        gloves = submit_request(session, member(session, "Priya"), item_id=item(session, "Gloves").id).request.id
    home = client.get("/api/home", headers=people["Priya"]).json()
    assert (home["me"]["committed_cents"], home["me"]["monthly_limit_cents"]) == (25_00, 100_00)
    assert [r["item"] for r in home["me"]["open_requests"]] == ["Gloves"]
    assert home["group"]["overall"]["budget_cents"] == 300_00 and home["paypal_email_set"] is True

    assert client.post(f"/api/requests/{gloves}/release", headers=people["Marco"]).status_code == 400  # not his
    assert client.post(f"/api/requests/{gloves}/release", headers=people["Priya"]).status_code == 200
    assert client.get("/api/home", headers=people["Priya"]).json()["me"]["open_requests"] == []


def test_notifications_are_listed_and_marked_read(client, people):
    with Session(engine) as session:  # not on the list: the PICs are told
        submit_request(session, member(session, "Priya"), description="Folding table", estimate_cents=40_00,
                       reason="For the stall")
    home = client.get("/api/home", headers=people["Dana"]).json()
    assert home["unread"] == 1 and "Priya wants to get Folding table" in home["notifications"][0]["text"]
    assert client.post("/api/notifications/read", json={}, headers=people["Dana"]).json() == {"marked": 1}
    assert client.get("/api/home", headers=people["Dana"]).json()["unread"] == 0


# --- the feed ---------------------------------------------------------------------------------------

def test_the_feed_shows_who_got_what_and_takes_a_like(client, people):
    bought("Priya", "Trash bags", 11_99)
    posts = client.get("/api/feed", headers=people["Marco"]).json()
    assert [(p["kind"], p["text"], p["status"]) for p in posts] == [
        ("got_it", "Priya got Trash bags, $11.99", "waiting"), ("on_it", "Priya is getting Trash bags", "claimed")]
    got = posts[0]
    assert got["can_flag"] is True and got["claim_id"]
    assert client.get("/api/feed", headers=people["Priya"]).json()[0]["can_flag"] is False  # her own

    assert client.post(f"/api/feed/{got['post_id']}/like", headers=people["Marco"]).json() == {"likes": 1}
    assert client.get("/api/feed", headers=people["Marco"]).json()[0]["liked"] is True
    assert client.post(f"/api/feed/{got['post_id']}/like", headers=people["Marco"]).json() == {"likes": 0}


def test_somethings_wrong_asks_the_buyer_first_then_a_formal_report(client, people):
    _, claim_id = bought("Priya", "Trash bags")
    asked = client.post(f"/api/claims/{claim_id}/ask", json={"text": "Can't find the bags"}, headers=people["Marco"])
    assert asked.status_code == 200
    [question] = client.get("/api/home", headers=people["Priya"]).json()["questions_for_me"]
    assert (question["asked_by"], question["text"]) == ("Marco", "Can't find the bags")
    answered = client.post(f"/api/questions/{question['question_id']}/answer", json={"answer": "In the van"},
                           headers=people["Priya"])
    assert answered.status_code == 200

    missing = client.post(f"/api/claims/{claim_id}/report", json={"text": "Still not there", "checked": " "},
                          headers=people["Marco"])
    assert missing.status_code == 400 and "what you checked" in missing.json()["detail"]
    filed = client.post(f"/api/claims/{claim_id}/report", json={"text": "Still not there",
                                                                "checked": "The van and the cupboard"},
                        headers=people["Marco"])
    assert filed.status_code == 200
    assert client.get("/api/feed", headers=people["Marco"]).json()[0]["status"] == "held"  # the hold is extended


# --- chat -------------------------------------------------------------------------------------------

def scripted(*steps):
    steps = iter(steps)
    return FakeChatModel(respond=lambda messages, tools: next(steps))


@pytest.fixture
def fake_ai(monkeypatch):
    """The coordinator asks lookup once, then answers. Lookup says something in passing that must not leak."""
    lookup = LookupAgent(model=scripted(call("LookupResult", {"status": "not_found"}, text="INTERNAL lookup notes")))
    desk = scripted(call("lookup", {"request": "Who got coffee?"}), "Marco got the coffee yesterday.")
    monkeypatch.setattr(ai_main, "_coordinator", ai_main.Coordinator(model=desk, lookup=lookup,
                                                                      action=ActionAgent(model=scripted())))


def test_a_chat_reply_is_streamed_without_the_helpers_insides(client, people, fake_ai):
    with client.stream("POST", "/api/chat", json={"text": "Who got the coffee?"}, headers=people["Dana"]) as r:
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert {"type": "step", "text": "Checking the group's records…"} in events
    assert events[-1] == {"type": "done", "text": "Marco got the coffee yesterday.", "failed": False}
    assert "INTERNAL" not in json.dumps(events)
    streamed = "".join(e["text"] for e in events if e["type"] == "text")
    assert streamed.endswith("Marco got the coffee yesterday.")

    history = client.get("/api/chat", headers=people["Dana"]).json()
    assert [(m["role"], m["text"]) for m in history["messages"]] == [
        ("user", "Who got the coffee?"), ("assistant", "Marco got the coffee yesterday.")]
    assert client.get("/api/chat", headers=people["Marco"]).json()["messages"] == []  # each member's own chat


def test_an_empty_message_is_refused(client, people):
    assert client.post("/api/chat", json={"text": "  "}, headers=people["Dana"]).status_code == 400
