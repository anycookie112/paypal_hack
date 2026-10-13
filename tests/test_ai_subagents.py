"""The lookup and action subagents against the demo group, with a fake model choosing the tool calls."""

import asyncio
import json
from datetime import timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlmodel import select

from app.ai import AgentError
from app.ai.agents import ActionAgent, ActionResult, Chat, LookupAgent
from app.core import settings
from app.models import Notification, Report, Request
from tests.demo import NOW, item, member, past_purchase, tell
from tests.fakes import FakeChatModel, call


def scripted(*steps):
    """A fake model that makes these replies in order and keeps the tool results it was sent."""
    results, steps = [], iter(steps)

    def respond(messages, tools):
        if isinstance(messages[-1], ToolMessage):
            results.append(messages[-1])
        return next(steps)
    return FakeChatModel(respond=respond), results


def found(answer="ok"):
    return call("LookupResult", {"status": "found", "answer": answer})


def done(message="Done."):
    return call("ActionResult", {"status": "done", "message": message})


def ask(agent, session, who, request="go"):
    return asyncio.run(agent.run(request, context=Chat(session, member(session, who), now=NOW)))


# --- lookup ---------------------------------------------------------------------------------------

def test_lookup_finds_the_item_and_who_is_on_it(session, group):
    tell(session, "Marco", "Trash bags")
    model, results = scripted(call("find_items", {"query": "work gloves"}), call("who_is_on_it"),
                              found("Gloves (item 1, up to $25.00); Marco is getting trash bags."))
    result = ask(LookupAgent(model=model), session, "Priya")
    assert (result.status, result.answer[:6]) == ("found", "Gloves")
    [gloves] = json.loads(results[0].content)
    assert (gloves["item_id"], gloves["name"], gloves["cap"]) == (item(session, "Gloves").id, "Gloves", "$25.00")
    [getting] = json.loads(results[1].content)
    assert (getting["member"], getting["item"], getting["until"]) == ("Marco", "Trash bags", "Sat 17 Oct, 12:40 UTC")


def test_lookup_knows_who_is_chatting_and_when(session, group):
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or found())
    ask(LookupAgent(model=model), session, "Priya")
    assert seen[0].endswith("The member chatting is Priya (member). It's Sat 17 Oct, 08:40 UTC.")


def test_lookup_says_who_got_it_and_hides_amounts_if_the_group_does(session, group):
    past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    settings.set_value(session, group, "feed_shows_amounts", False)
    gloves = item(session, "Gloves").id
    model, results = scripted(call("latest_purchase", {"item_id": gloves}), found("Priya got them yesterday."))
    ask(LookupAgent(model=model), session, "Marco")
    details = json.loads(results[0].content)
    assert (details["member"], details["total"]) == ("Priya", None)

    model, results = scripted(call("latest_purchase", {"item_id": gloves}), found("You got them yesterday."))
    ask(LookupAgent(model=model), session, "Priya")
    assert json.loads(results[0].content)["total"] == "$18.40"  # her own purchase


def test_lookup_my_status_and_rules(session, group):
    model, results = scripted(call("my_status"), call("group_rules"), found())
    ask(LookupAgent(model=model), session, "Priya")
    status = json.loads(results[0].content)
    assert (status["status"]["monthly_limit"], status["paypal_email_set"]) == ("$100.00", False)
    rules = json.loads(results[1].content)
    assert (rules["pics"], rules["treasurer"]) == (["Dana", "Sam"], ["Sam"])
    assert "Most hours a member can take to buy a confirmed item: 72" in rules["settings"]


# --- action ---------------------------------------------------------------------------------------

def test_getting_an_item_by_the_members_deadline(session, group):
    gloves = item(session, "Gloves").id
    model, results = scripted(call("get_item", {"item_id": gloves, "hours": 2}), done("You're on it!"))
    assert ask(ActionAgent(model=model), session, "Priya") == ActionResult(status="done", message="You're on it!")
    assert results[0].content.startswith("Confirmed (request 1): Priya is getting Gloves, paid back up to $25.00, "
                                         "by Sat 17 Oct, 10:40 UTC.")
    request = session.exec(select(Request)).one()
    assert (request.member_id, request.status, request.on_it_until) == (
        member(session, "Priya").id, "on_it", NOW + timedelta(hours=2))


def test_someone_already_getting_it(session, group):
    tell(session, "Marco", "Gloves")
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id}), done("Marco has it."))
    ask(ActionAgent(model=model), session, "Priya")
    assert results[0].content.startswith("Not recorded: Marco is getting it (until Sat 17 Oct, 12:40 UTC).")


def test_rules_saying_no_come_back_to_the_agent(session, group):
    model, results = scripted(call("get_item", {"description": "Folding table", "estimate": "40"}),
                              call("get_item", {"description": "Folding table", "estimate": "40",
                                                "reason": "For the stall"}), done("Sent to Dana and Sam."))
    ask(ActionAgent(model=model), session, "Priya")
    assert results[0].status == "error" and "Say why it's needed." in results[0].content
    assert results[1].content.startswith("Sent to a PIC to approve (request 1): Not on the group's list")
    assert session.exec(select(Request)).one().status == "needs_approval"


def test_actions_are_only_ever_the_members_own(session, group):
    request = tell(session, "Marco", "Gloves").request
    model, results = scripted(call("release", {"request_id": request.id}), call("ActionResult", {"status": "refused", "message": "Only Marco can."}))
    ask(ActionAgent(model=model), session, "Priya")
    assert results[0].status == "error" and "Only Marco can release it." in results[0].content
    session.refresh(request)
    assert request.status == "on_it"


def test_asking_the_buyer_then_a_formal_report(session, group):
    claim = past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    model, results = scripted(call("ask_buyer", {"claim_id": claim.id, "question": "Not in the supply box"}),
                              call("report_problem", {"claim_id": claim.id, "what_is_wrong": "Still missing",
                                                      "checked": "The supply box; asked Priya"}), done())
    ask(ActionAgent(model=model), session, "Marco")
    assert results[0].content.startswith("Asked (question 1).")
    assert results[1].content.startswith("Report 2 filed")
    assert [r.kind for r in session.exec(select(Report)).all()] == ["question", "report"]
    assert session.exec(select(Notification).where(Notification.kind == "question")).one().member_id == \
        member(session, "Priya").id


def test_setting_the_paypal_email(session, group):
    model, results = scripted(call("set_paypal_email", {"email": "priya@example.com"}), done("Saved."))
    ask(ActionAgent(model=model), session, "Priya")
    assert results[0].content == "Payouts will go to priya@example.com."
    assert member(session, "Priya").paypal_email == "priya@example.com"


def test_an_action_that_happened_is_reported_even_if_the_agent_fails(session, group):
    """The request is recorded and the group sees it; then the model never finishes. The coordinator must still
    hear that Priya is down for the gloves, or it might tell her it didn't work."""
    gloves = item(session, "Gloves").id
    model, _ = scripted(call("get_item", {"item_id": gloves, "hours": 2}), "Sure!", "Sure!")
    with pytest.raises(AgentError, match="act") as failure:
        ask(ActionAgent(model=model, max_model_calls=3), session, "Priya")
    [done_already] = failure.value.completed
    assert done_already["tool"] == "get_item"
    assert done_already["result"].startswith("Confirmed (request 1): Priya is getting Gloves")
    assert session.exec(select(Request)).one().status == "on_it"  # it really happened
