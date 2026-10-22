"""The Lab API: the agent log for the whole group, where things stand, and starting the demo over."""

import pytest
from sqlmodel import Session, select

from app.core import memory
from app.core.demo import seed
from app.core.requests import submit_request
from app.db import engine
from app.models import Item, Member, Request


@pytest.fixture
def people(client):
    """The demo group, as seeded for the demo; name -> X-Member-Id header."""
    with Session(engine) as session:
        seed(session)
        return {m.name: {"X-Member-Id": str(m.id)} for m in session.exec(select(Member))}


def gloves_for(name: str) -> int:
    with Session(engine) as session:
        priya = session.exec(select(Member).where(Member.name == name)).one()
        gloves = session.exec(select(Item).where(Item.name == "Gloves")).one()
        return submit_request(session, priya, item_id=gloves.id).request.id


def test_the_log_shows_every_members_ai_steps(client, people):
    with Session(engine) as session:
        for name, tool in (("Priya", "act"), ("Marco", "lookup")):
            chat = memory.get_conversation(session, session.exec(select(Member).where(Member.name == name)).one())
            memory.log_step(session, agent="coordinator", event="tool_call", conversation=chat, name=tool,
                            data={"request": "…"}, prompt_id="coordinator@v8")
    entries = client.get("/api/lab/log", headers=people["Dana"]).json()
    assert [(e["member"], e["agent"], e["event"], e["name"], e["prompt_id"]) for e in entries] == [
        ("Priya", "coordinator", "tool_call", "act", "coordinator@v8"),
        ("Marco", "coordinator", "tool_call", "lookup", "coordinator@v8")]
    newer = client.get(f"/api/lab/log?after={entries[0]['id']}", headers=people["Dana"]).json()
    assert [e["member"] for e in newer] == ["Marco"]  # only what's new since the last poll


def test_status_shows_open_requests_and_moves(client, people):
    request_id = gloves_for("Priya")
    status = client.get("/api/lab/status", headers=people["Sam"]).json()
    assert [(r["id"], r["member"], r["item"], r["status"]) for r in status["requests"]] == [
        (request_id, "Priya", "Gloves", "on_it")]
    assert status["claims"] == []
    assert status["moves"][0]["text"].endswith(f"Priya's Gloves (request {request_id}): new → getting it "
                                               "(the rules confirmed it)")


def test_reset_starts_the_demo_over(client, people):
    gloves_for("Priya")
    assert client.post("/api/lab/reset", headers=people["Priya"]).json() == {"reset": True}
    with Session(engine) as session:
        assert session.exec(select(Request).where(Request.status == "on_it")).all() == []
        assert sorted(m.name for m in session.exec(select(Member))) == ["Dana", "Marco", "Priya", "Sam"]
    assert client.get("/api/lab/status", headers=people["Priya"]).status_code == 200  # same ids after a reset
