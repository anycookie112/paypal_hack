"""The lookup and action subagents against the demo group, with a fake model choosing the tool calls."""

import asyncio
import json
from datetime import timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlmodel import select

from app.ai import AgentError
from app.ai.agents import ActionAgent, ActionResult, Chat, LookupAgent
from app.core import memory, payouts, settings
from app.core.requests import approve_request
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


def ask(agent, session, who, request="go", conversation=None):
    return asyncio.run(agent.run(request, context=Chat(session, member(session, who), now=NOW,
                                                       conversation=conversation)))


def said(session, who, *texts):
    """A conversation in which `who` wrote these messages: what quoted times are checked against."""
    conversation = memory.get_conversation(session, member(session, who))
    for text in texts:
        memory.add_message(session, conversation, "user", text)
    return conversation


# --- lookup ---------------------------------------------------------------------------------------

def test_every_agent_sees_the_items_grouped_by_where_they_stand(session, group):
    past_purchase(session, "Priya", "Trash bags", 11_99, days_ago=16)      # lasts 14: running low
    past_purchase(session, "Marco", "Coffee", 14_50, days_ago=5)           # lasts 14: too soon to get again
    past_purchase(session, "Dana", "Snacks and water", 20_00, days_ago=5)  # lasts 7: fine to get again
    marco = tell(session, "Marco", "Gloves").request
    ladder = tell(session, "Dana", description="Ladder", estimate_cents=40_00).request  # not on the list
    approve_request(session, ladder.id, member(session, "Sam"), now=NOW)
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or found())
    ask(LookupAgent(model=model), session, "Priya")

    def section(name):
        text = seen[-1].split(f"<{name}>\n")[1].split(f"\n</{name}>")[0]
        return [line.split(" (")[0][2:] for line in text.splitlines() if line.startswith("- ")], text

    getting, text = section("being_got")
    assert getting == ["Gloves", "Ladder"]
    assert f"  Marco, until Sat 17 Oct, 12:40 UTC (request {marco.id})" in text
    assert "- Ladder (not on the list; a PIC approved it)" in text
    assert section("running_low")[0] == ["Trash bags"]
    assert "  last bought 16 days ago by Priya" in section("running_low")[1]
    assert section("recently_bought")[0] == ["Coffee"]
    assert section("available")[0] == ["Cleaning wipes", "First-aid restock", "Flyer printing", "Snacks and water",
                                       "Tape and markers"]
    assert "up to $25.00 | lasts about 14 days | proof: receipt + photo" in section("being_got")[1]

    gloves = item(session, "Gloves")
    gloves.active = False  # switched off: not offered any more
    session.add(gloves)
    session.commit()
    ask(LookupAgent(model=model), session, "Priya")
    assert "- Gloves" not in seen[-1]


def test_the_item_list_agrees_with_the_decision_engine(session, group):
    """What the AI is told is "recently bought" is exactly what the rules send to a PIC as bought too soon."""
    past_purchase(session, "Marco", "Coffee", 14_50, days_ago=5)
    board = memory.item_board(session, group, NOW)
    assert [i.name for i in board.with_status("recently_bought")] == ["Coffee"]
    result = tell(session, "Priya", "Coffee")
    assert result.decision.outcome == "needs_approval" and "bought 5 days ago by Marco" in result.decision.reasons[0]
    assert tell(session, "Priya", "Tape and markers").decision.outcome == "confirmed"  # in <available>


def test_lookup_knows_who_is_chatting_and_when(session, group):
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or found())
    ask(LookupAgent(model=model), session, "Priya")
    assert "<chat_context>\nGroup: Riverside Volunteers. Member: Priya (member). Now: Sat 17 Oct, 08:40 UTC.\n\n" \
           "<member>" in seen[0]
    assert "<finish>\n- When you're done, call LookupResult exactly once." in seen[0]
    assert '- Example: {"status": "found"' in seen[0]


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


def test_every_agent_sees_the_members_own_status(session, group):
    claim = past_purchase(session, "Priya", "Coffee", 14_50, days_ago=1)
    payouts.start_hold(session, claim, now=NOW - timedelta(days=1))
    payouts.ask_buyer(session, claim.id, member(session, "Marco"), "The coffee isn't in the cupboard", now=NOW)
    gloves = tell(session, "Priya", "Gloves", now=NOW - timedelta(minutes=1)).request
    table = tell(session, "Priya", description="Folding table", estimate_cents=45_00,
                 now=NOW - timedelta(hours=3)).request
    tell(session, "Marco", "Trash bags")  # someone else's: not in Priya's profile
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or found())
    ask(LookupAgent(model=model), session, "Priya")
    profile = seen[0].split("<member>\n")[1].split("\n</member>")[0].splitlines()
    assert profile == [
        "Priya (member). PayPal email for payouts: not set yet (needed before their first payout).",
        "This month: $39.50 of Priya's $100.00 limit used or promised.",
        "Priya's open requests:",
        f"- Folding table: waiting for a PIC (request {table.id}, asked 3 hours ago)",
        f"- Gloves: on it until Sat 17 Oct, 12:39 UTC (request {gloves.id}, confirmed just now)",
        "Priya's receipts waiting for payout:",
        f"- Coffee, $14.50: paid on Mon 19 Oct (claim {claim.id})",
        "Questions others asked about Priya's purchases:",
        "- Marco, about Coffee: \"The coffee isn't in the cupboard\" (question 1)",
    ]


def test_lookup_group_rules(session, group):
    model, results = scripted(call("group_rules"), found())
    ask(LookupAgent(model=model), session, "Priya")
    rules = json.loads(results[0].content)
    assert (rules["pics"], rules["treasurer"]) == (["Dana", "Sam"], ["Sam"])
    assert "Most hours a member can take to buy a confirmed item: 72" in rules["settings"]


# --- action ---------------------------------------------------------------------------------------

def test_getting_an_item_by_the_members_deadline(session, group):
    gloves = item(session, "Gloves").id
    model, results = scripted(call("get_item", {"item_id": gloves, "when_said": "by 11"}), done("You're on it!"))
    chat = said(session, "Priya", "We're out of gloves", "I'll get them by 11")
    assert ask(ActionAgent(model=model), session, "Priya", conversation=chat) == \
        ActionResult(status="done", message="You're on it!")
    assert results[0].content.startswith("Confirmed (request 1): Priya is getting Gloves, paid back up to $25.00, "
                                         "by Sat 17 Oct, 11:00 UTC. The group")  # exactly what she said
    request = session.exec(select(Request)).one()
    assert (request.member_id, request.status, request.on_it_until) == (
        member(session, "Priya").id, "on_it", NOW.replace(hour=11, minute=0))


def test_no_time_said_means_the_usual_time(session, group):
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id}), done())
    ask(ActionAgent(model=model), session, "Priya", conversation=said(session, "Priya", "I'll grab gloves"))
    assert "by Sat 17 Oct, 12:40 UTC (the group's usual time: they didn't say when, and can change it)" in \
        results[0].content


def test_a_time_the_member_never_said_is_refused(session, group):
    """A live run had the model invent "10 hours" when the member hadn't said a time."""
    gloves = item(session, "Gloves").id
    model, results = scripted(call("get_item", {"item_id": gloves, "when_said": "in 10 hours"}),
                              call("get_item", {"item_id": gloves}), done())
    ask(ActionAgent(model=model), session, "Priya", conversation=said(session, "Priya", "I'll grab gloves"))
    assert results[0].status == "error" and "isn't something the member wrote" in results[0].content
    assert session.exec(select(Request)).one().on_it_until == NOW + timedelta(hours=4)


def test_changing_the_time_with_the_members_words(session, group):
    request = tell(session, "Priya", "Gloves").request
    model, results = scripted(call("change_time", {"request_id": request.id, "when_said": "by 11"}), done())
    ask(ActionAgent(model=model), session, "Priya", conversation=said(session, "Priya", "I'll grab gloves", "by 11"))
    assert results[0].content == f"Changed (request {request.id}): Priya now has until Sat 17 Oct, 11:00 UTC to get Gloves."


def test_an_unclear_time_asks_for_a_clock_time(session, group):
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id, "when_said": "after lunch"}),
                              call("ActionResult", {"status": "needs_info", "message": "When exactly?",
                                                    "missing": ["a clock time"]}))
    result = ask(ActionAgent(model=model), session, "Priya",
                 conversation=said(session, "Priya", "I'll grab gloves after lunch"))
    assert results[0].status == "error" and "ask them for a clock time" in results[0].content
    assert result.status == "needs_info" and session.exec(select(Request)).all() == []


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
    assert results[0].status == "error" and "Missing: why it's needed." in results[0].content
    assert results[1].content.startswith("Sent to a PIC to approve (request 1): Not on the group's list")
    assert session.exec(select(Request)).one().status == "needs_approval"


def test_actions_are_only_ever_the_members_own(session, group):
    request = tell(session, "Marco", "Gloves").request
    model, results = scripted(call("release", {"request_id": request.id}), call("ActionResult", {"status": "refused", "message": "Only Marco can."}))
    ask(ActionAgent(model=model), session, "Priya")
    assert results[0].status == "error" and "Only Marco can change that request." in results[0].content
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
