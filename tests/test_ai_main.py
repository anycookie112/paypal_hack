"""The coordinator with its subagents, and `reply`: one chat message end to end (fake models, the demo group)."""

import asyncio
import json
from datetime import timedelta

from sqlmodel import select

from app.ai.agents import ActionAgent, LookupAgent
from app.ai.main import SORRY, SORRY_PARTLY, Coordinator, reply, stream_reply
from app.models import AgentLog, Message, Request
from tests.demo import NOW, item, member, tell
from tests.fakes import FakeChatModel, call, copying


def scripted(*steps):
    """A fake model giving these replies in order (a step can be a function of the messages so far); it keeps
    every message list it was sent."""
    seen, steps = [], iter(steps)

    def respond(messages, tools):
        seen.append(messages)
        step = next(steps)
        return step(messages) if callable(step) else step
    return FakeChatModel(respond=respond), seen


def gloves_flow(session, final="You're on it: gloves, up to $25, by 12:40. The group can see it."):
    """The coordinator finds gloves in the item list itself (nobody is on them), then asks act to get them."""
    gloves = item(session, "Gloves").id
    action, _ = scripted(call("get_item", {"item_id": gloves}),
                         copying("ActResult", "actions", "done"))
    desk, desk_seen = scripted(call("act", {"request": f"Priya is getting Gloves (item_id {gloves})."}), final)
    unused, _ = scripted()  # lookup isn't needed: which item and who's on it are in the item list
    agent = Coordinator(model=desk, lookup=LookupAgent(model=unused), action=ActionAgent(model=action))
    return agent, desk_seen


def test_a_message_end_to_end(session, group):
    agent, desk_seen = gloves_flow(session)
    answer = asyncio.run(reply(session, member(session, "Priya"), "We're out of gloves, I'll grab some",
                               agent=agent, now=NOW))
    assert answer.text.startswith("You're on it") and not answer.failed
    request = session.exec(select(Request)).one()
    assert (request.status, request.on_it_until) == ("on_it", NOW + timedelta(hours=4))

    # what the coordinator saw: its prompt with who's chatting, their status and the item list, then act's result
    prompt = desk_seen[0][0].content
    assert "Member: Priya (member). Now: Sat 17 Oct, 08:40 UTC.\n\n<member>\nPriya (member)." in prompt
    assert "</member>\n\n<items>" in prompt
    assert prompt.endswith("</items>\n</chat_context>")
    assert "<available>" in prompt and "- Gloves (item_id 1; also called work gloves" in prompt
    assert "<finish>" not in prompt  # the coordinator answers in text
    act_said = json.loads(desk_seen[1][-1].content)  # typed fields, checked against what get_item returned
    assert (act_said["status"], act_said["missing"], act_said["refusal"]) == ("done", [], None)
    assert [(a["type"], a["outcome"], a["request_id"]) for a in act_said["actions"]] == [
        ("item_requested", "confirmed", 1)]

    # the conversation is kept, and every step is in the agent log
    saved = session.exec(select(Message).order_by(Message.id)).all()
    assert [(m.role, m.text[:12]) for m in saved] == [("user", "We're out of"), ("assistant", "You're on it")]
    log = session.exec(select(AgentLog).order_by(AgentLog.id)).all()
    steps = [(s.agent, s.event, s.name) for s in log if s.event != "model"]
    assert steps[0] == ("coordinator", "tool_call", "act")
    assert ("act", "tool_result", "get_item") in steps
    assert steps[-1] == ("coordinator", "output", "")

    # where the time went: every model call (2 for the coordinator, 2 for act) and tool call is timed
    models = [(s.agent, json.loads(s.data_json)) for s in log if s.event == "model"]
    assert [agent for agent, _ in models] == ["coordinator", "act", "act", "coordinator"]
    assert all(isinstance(data["ms"], int) and "input_tokens" in data for _, data in models)
    assert all(isinstance(json.loads(s.data_json)["ms"], int) for s in log if s.event == "tool_result")
    assert all(s.conversation_id == answer.conversation_id for s in session.exec(select(AgentLog)))


def test_follow_ups_see_the_conversation(session, group):
    priya = member(session, "Priya")
    first, _ = scripted("When will you get them?")
    asyncio.run(reply(session, priya, "We're out of gloves", agent=Coordinator(model=first), now=NOW))
    second, seen = scripted("Got it.")
    asyncio.run(reply(session, priya, "By 1 pm", agent=Coordinator(model=second), now=NOW))
    assert [m.content for m in seen[0][1:]] == ["We're out of gloves", "When will you get them?", "By 1 pm"]


def test_when_the_coordinator_fails_the_member_gets_an_apology(session, group):
    looping = FakeChatModel(respond=lambda messages, tools: call("lookup", {"request": f"again {len(messages)}"}))
    lookup = LookupAgent(model=FakeChatModel(respond=lambda m, t: call("LookupResult", {"status": "not_found"})))
    answer = asyncio.run(reply(session, member(session, "Priya"), "hi", now=NOW,
                               agent=Coordinator(model=looping, lookup=lookup, max_model_calls=2)))
    assert answer.failed and answer.text == SORRY  # lookups change nothing
    error = session.exec(select(AgentLog).where(AgentLog.event == "error")).one()
    assert json.loads(error.data_json)["reason"].startswith("coordinator:")

    nothing_done = FakeChatModel(respond=lambda messages, tools: (_ for _ in ()).throw(TimeoutError()))
    answer = asyncio.run(reply(session, member(session, "Marco"), "hi", now=NOW,
                               agent=Coordinator(model=nothing_done)))
    assert answer.failed and answer.text == SORRY
    assert [m.role for m in session.exec(select(Message).where(Message.text == SORRY))] == ["assistant", "assistant"]


def test_a_streamed_reply(session, group):
    agent, _ = gloves_flow(session, final="You're on it!")

    async def collect():
        return [e async for e in stream_reply(session, member(session, "Priya"), "gloves", agent=agent, now=NOW)]
    events = asyncio.run(collect())
    assert [e.name for e in events if e.type == "tool_call"] == ["act"]
    assert "".join(e.text for e in events if e.type == "text") == "You're on it!"
    assert events[-1].type == "output" and events[-1].data.text == "You're on it!"
    assert session.exec(select(Message).where(Message.role == "assistant")).one().text == "You're on it!"


def test_an_apology_says_when_something_already_happened(session, group):
    agent, _ = gloves_flow(session)
    agent.max_model_calls = 1  # out of calls right after act got the gloves
    answer = asyncio.run(reply(session, member(session, "Priya"), "gloves", agent=agent, now=NOW))
    assert answer.failed and answer.text == SORRY_PARTLY
    assert session.exec(select(Request)).one().status == "on_it"


def test_the_coordinator_hears_what_moved_since_its_last_reply(session, group):
    """A live run told Marco "I was wrong" about gloves Priya had bought after it spoke: nothing told it they had
    moved since."""
    marco = member(session, "Marco")
    first, seen = scripted("Priya is getting them.")
    asyncio.run(reply(session, marco, "Is anyone getting gloves?", agent=Coordinator(model=first), now=NOW))
    assert seen[0][0].content.endswith("</items>\n</chat_context>")  # nothing said yet: nothing moved since
    said = session.exec(select(Message).where(Message.role == "assistant")).one()
    said.created_at = NOW  # the reply's time, on the test's clock
    session.add(said)
    session.commit()
    gloves = tell(session, "Priya", "Gloves", now=NOW + timedelta(minutes=1)).request

    second, seen = scripted("She's on it.")
    asyncio.run(reply(session, marco, "And now?", agent=Coordinator(model=second), now=NOW + timedelta(minutes=2)))
    prompt = seen[0][0].content
    assert prompt.index("\n<lifecycle>\n") < prompt.index("\n<chat_context>\n")  # fixed, so before the run's facts
    assert prompt.endswith(
        "<since_your_last_reply>\nYour replies were true when you sent them. What moved in the group since your last "
        "reply (08:40 UTC), oldest first:\n"
        f"- 08:41 UTC Priya's Gloves (request {gloves.id}): new → getting it (the rules confirmed it)\n"
        "</since_your_last_reply>\n</chat_context>")
