"""The lookup and action subagents against the demo group, with a fake model choosing the tool calls."""

import asyncio
import json
from datetime import timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlmodel import select

from app.ai import AgentError
from app.ai.agents import ActionAgent, Chat, LookupAgent
from app.core import memory, payouts, settings
from app.core.requests import approve_request
from app.models import Member, Notification, Report, Request
from tests.demo import NOW, item, member, past_purchase, tell
from tests.fakes import FakeChatModel, call, copying


def scripted(*steps):
    """A fake model that makes these replies in order (a step can be a function of the messages so far) and keeps
    the tool results it was sent."""
    results, steps = [], iter(steps)

    def respond(messages, tools):
        if isinstance(messages[-1], ToolMessage):
            results.append(messages[-1])
        step = next(steps)
        return step(messages) if callable(step) else step
    return FakeChatModel(respond=respond), results


def found():
    """Lookup finishes with every fact its tools returned."""
    return copying("LookupResult", "facts", "found")


def nothing_found():
    return call("LookupResult", {"status": "not_found"})


def done():
    """Act finishes with every result its tools returned."""
    return copying("ActResult", "actions", "done")


def refused(messages):
    """Act finishes refused, with the last tool's refusal copied."""
    said_no = [m for m in messages if isinstance(m, ToolMessage) and m.status == "error" and m.name != "ActResult"]
    return call("ActResult", {"status": "refused", "refusal": said_no[-1].content})


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
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or nothing_found())
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
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or nothing_found())
    ask(LookupAgent(model=model), session, "Priya")
    assert "<chat_context>\nGroup: Riverside Volunteers. Member: Priya (member). Now: Sat 17 Oct, 08:40 UTC.\n\n" \
           "<member>" in seen[0]
    assert "<finish>\n- When you're done, call LookupResult exactly once." in seen[0]
    assert '- Example: {"status": "found"' in seen[0]


def test_lookup_says_who_got_it_and_hides_amounts_if_the_group_does(session, group):
    past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    settings.set_value(session, group, "feed_shows_amounts", False)
    gloves = item(session, "Gloves").id
    model, results = scripted(call("latest_purchase", {"item_id": gloves}), found())
    result = ask(LookupAgent(model=model), session, "Marco")
    details = json.loads(results[0].content)
    assert (details["type"], details["buyer"], details["total"], details["item_id"]) == ("purchase", "Priya", None,
                                                                                          gloves)
    assert result.status == "found" and result.facts[0].model_dump() == details  # copied, and checked

    model, results = scripted(call("latest_purchase", {"item_id": gloves}), found())
    ask(LookupAgent(model=model), session, "Priya")
    assert json.loads(results[0].content)["total"] == "$18.40"  # her own purchase


def test_every_agent_sees_the_members_own_status(session, group):
    claim = past_purchase(session, "Priya", "Coffee", 14_50, days_ago=1)
    claim.status = "review"  # not paid yet: a PIC accepted it yesterday, so it's in its hold
    payouts.start_hold(session, claim, now=NOW - timedelta(days=1))
    payouts.ask_buyer(session, claim.id, member(session, "Marco"), "The coffee isn't in the cupboard", now=NOW)
    gloves = tell(session, "Priya", "Gloves", now=NOW - timedelta(minutes=1)).request
    table = tell(session, "Priya", description="Folding table", estimate_cents=45_00,
                 now=NOW - timedelta(hours=3)).request
    tell(session, "Marco", "Trash bags")  # someone else's: not in Priya's profile
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or nothing_found())
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
    model, results = scripted(call("get_item", {"item_id": gloves, "when_said": "by 11"}), done())
    chat = said(session, "Priya", "We're out of gloves", "I'll get them by 11")
    result = ask(ActionAgent(model=model), session, "Priya", conversation=chat)
    assert json.loads(results[0].content) == {
        "type": "item_requested", "outcome": "confirmed", "request_id": 1, "item": "Gloves",
        "paid_back_up_to": "$25.00", "until": "Sat 17 Oct, 11:00 UTC", "usual_time": False,  # exactly what she said
        "reasons": [], "price": None, "reason_given": None, "already_getting_it": []}
    assert result.status == "done" and result.actions[0].model_dump() == json.loads(results[0].content)
    request = session.exec(select(Request)).one()
    assert (request.member_id, request.status, request.on_it_until) == (
        member(session, "Priya").id, "on_it", NOW.replace(hour=11, minute=0))


def test_no_time_said_means_the_usual_time(session, group):
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id}), done())
    ask(ActionAgent(model=model), session, "Priya", conversation=said(session, "Priya", "I'll grab gloves"))
    got = json.loads(results[0].content)
    assert (got["until"], got["usual_time"]) == ("Sat 17 Oct, 12:40 UTC", True)


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
    assert json.loads(results[0].content) == {"type": "time_changed", "request_id": request.id, "item": "Gloves",
                                              "until": "Sat 17 Oct, 11:00 UTC"}


def test_an_unclear_time_asks_for_a_clock_time(session, group):
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id, "when_said": "after lunch"}),
                              call("ActResult", {"status": "needs_info", "missing": ["a clock time"]}))
    result = ask(ActionAgent(model=model), session, "Priya",
                 conversation=said(session, "Priya", "I'll grab gloves after lunch"))
    assert results[0].status == "error" and "ask them for a clock time" in results[0].content
    assert result.status == "needs_info" and session.exec(select(Request)).all() == []


def test_someone_already_getting_it(session, group):
    tell(session, "Marco", "Gloves")
    model, results = scripted(call("get_item", {"item_id": item(session, "Gloves").id}), done())
    ask(ActionAgent(model=model), session, "Priya")
    got = json.loads(results[0].content)
    assert (got["outcome"], got["request_id"]) == ("already_on_it", None)  # nothing recorded
    assert got["already_getting_it"] == [{"member": "Marco", "until": "Sat 17 Oct, 12:40 UTC", "request_id": 1}]


def test_rules_saying_no_come_back_to_the_agent(session, group):
    """Something not on the list needs a price range and a reason before a PIC sees it: both asked for at once."""
    model, results = scripted(call("get_item", {"description": "Folding table"}),
                              call("get_item", {"description": "Folding table", "price_said": "30 to 50",
                                                "reason": "For the stall"}), done())
    ask(ActionAgent(model=model), session, "Priya",
        conversation=said(session, "Priya", "Can I get a folding table?", "30 to 50", "For the stall"))
    assert results[0].status == "error" and "Missing: a price range; why it's needed." in results[0].content
    got = json.loads(results[1].content)
    assert (got["outcome"], got["request_id"], got["price"], got["reason_given"]) == (
        "needs_approval", 1, "$30.00–$50.00", "For the stall")
    assert got["reasons"][0].startswith("Not on the group's list")
    request = session.exec(select(Request)).one()
    assert (request.status, request.estimate_low_cents, request.estimate_cents) == ("needs_approval", 30_00, 50_00)


def test_a_price_the_member_never_said_is_refused(session, group):
    model, results = scripted(call("get_item", {"description": "Folding table", "price_said": "about $40",
                                                "reason": "For the stall"}),
                              call("ActResult", {"status": "needs_info", "missing": ["a price range"]}))
    ask(ActionAgent(model=model), session, "Priya",
        conversation=said(session, "Priya", "Can I get a folding table?", "For the stall"))
    assert results[0].status == "error" and "isn't something the member wrote" in results[0].content
    assert session.exec(select(Request)).all() == []


def test_actions_are_only_ever_the_members_own(session, group):
    request = tell(session, "Marco", "Gloves").request
    model, results = scripted(call("release", {"request_id": request.id}), refused)
    result = ask(ActionAgent(model=model), session, "Priya")
    assert results[0].status == "error" and "Only Marco can change that request." in results[0].content
    assert (result.status, result.refusal) == ("refused", results[0].content)
    session.refresh(request)
    assert request.status == "on_it"


def test_asking_the_buyer_then_a_formal_report(session, group):
    claim = past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    model, results = scripted(call("ask_buyer", {"claim_id": claim.id, "question": "Not in the supply box"}),
                              call("report_problem", {"claim_id": claim.id, "what_is_wrong": "Still missing",
                                                      "checked": "The supply box; asked Priya"}), done())
    ask(ActionAgent(model=model), session, "Marco")
    assert json.loads(results[0].content) == {"type": "asked", "question_id": 1, "claim_id": claim.id,
                                              "asked": "Priya"}
    assert json.loads(results[1].content) == {"type": "reported", "report_id": 2, "claim_id": claim.id}
    assert [r.kind for r in session.exec(select(Report)).all()] == ["question", "report"]
    assert session.exec(select(Notification).where(Notification.kind == "question")).one().member_id == \
        member(session, "Priya").id


def test_setting_the_paypal_email(session, group):
    model, results = scripted(call("set_paypal_email", {"email": "priya@example.com"}), done())
    ask(ActionAgent(model=model), session, "Priya")
    assert json.loads(results[0].content) == {"type": "paypal_email_set", "email": "priya@example.com"}
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
    assert json.loads(done_already["result"])["outcome"] == "confirmed"
    assert session.exec(select(Request)).one().status == "on_it"  # it really happened


# --- results are checked against what the tools returned -----------------------------------------

def rewritten(**changes):
    """Act finishes with the tools' results, but with some fields changed: a copy that doesn't match."""
    def step(messages):
        reply = done()(messages)
        reply.tool_calls[0]["args"]["actions"][0].update(changes)
        return reply
    return step


def test_a_result_that_doesnt_match_the_tools_is_sent_back_to_fix(session, group):
    gloves = item(session, "Gloves").id
    model, results = scripted(call("get_item", {"item_id": gloves}), rewritten(until="Sat 17 Oct, 11:00 UTC"),
                              done())
    result = ask(ActionAgent(model=model), session, "Priya")
    rejected = results[1]
    assert rejected.status == "error" and rejected.content.startswith("Not accepted: ActResult doesn't match")
    assert 'actions[0]: until is "Sat 17 Oct, 11:00 UTC" but the tool returned "Sat 17 Oct, 12:40 UTC"' in \
        rejected.content
    assert result.actions[0].until == "Sat 17 Oct, 12:40 UTC"  # the fixed copy got through
    assert len(session.exec(select(Request)).all()) == 1      # and nothing was done twice


def test_a_result_that_never_matches_fails_the_run(session, group):
    gloves = item(session, "Gloves").id
    model, _ = scripted(call("get_item", {"item_id": gloves}), *[rewritten(outcome="needs_approval")] * 3)
    with pytest.raises(AgentError, match="act: its ActResult didn't match") as failure:
        ask(ActionAgent(model=model), session, "Priya")
    assert [step["tool"] for step in failure.value.completed] == ["get_item"]  # still says what happened


def test_every_action_must_be_reported(session, group):
    gloves = item(session, "Gloves").id
    model, results = scripted(call("get_item", {"item_id": gloves}), call("ActResult", {"status": "done"}), done())
    ask(ActionAgent(model=model), session, "Priya")
    assert "status is done, but no tool returned a result" in results[1].content
    assert 'actions is missing a result a tool returned: {"type":"item_requested"' in results[1].content


def test_a_refusal_is_the_tools_own_words(session, group):
    request = tell(session, "Marco", "Gloves").request
    model, results = scripted(call("release", {"request_id": request.id}),
                              call("ActResult", {"status": "refused", "refusal": "Marco is on it."}), refused)
    result = ask(ActionAgent(model=model), session, "Priya")
    assert "refusal must be one tool's message, copied exactly" in results[1].content
    assert result.refusal == results[0].content


def test_nothing_to_do_it_with_is_cant_do(session, group):
    model, _ = scripted(call("ActResult", {"status": "cant_do"}))
    assert ask(ActionAgent(model=model), session, "Marco").status == "cant_do"


def test_lookup_cant_report_a_fact_no_tool_returned(session, group):
    invented = {"type": "budgets", "overall": {"name": "Riverside Volunteers", "budget": "$300.00",
                                                "committed": "$0.00", "left": "$300.00"}, "categories": []}
    model, results = scripted(call("LookupResult", {"status": "found", "facts": [invented]}),
                              call("budgets"), found())
    result = ask(LookupAgent(model=model), session, "Dana")
    assert "facts[0]: no tool returned a budgets in this run" in results[0].content
    assert result.facts[0].type == "budgets" and result.facts[0].categories  # the real one, with its categories


def test_one_categorys_budget(session, group):
    """A live run copied only the category it was asked about, and the check sent it back: now the tool does that."""
    model, results = scripted(call("budgets", {"category": "Bins"}), call("budgets", {"category": "supplies"}),
                              found())
    result = ask(LookupAgent(model=model), session, "Dana")
    assert results[0].status == "error" and results[0].content.startswith('No category called "Bins". The categories:')
    assert [c.name for c in result.facts[0].categories] == ["Supplies"]
    assert result.facts[0].overall.name == group.name


def test_lookups_closest_items_must_be_real(session, group):
    model, results = scripted(call("LookupResult", {"status": "not_found", "closest_item_ids": [999]}),
                              nothing_found())
    ask(LookupAgent(model=model), session, "Dana")
    assert "closest_item_ids: 999 isn't an item in <items>" in results[0].content


# --- reasons, and asking twice --------------------------------------------------------------------

def test_a_reason_must_be_the_members_own_words(session, group):
    """The demo run had act write its own reason ("not on the item list, so it needs a reason and approval")."""
    chat = said(session, "Marco", "Can I get a folding table for the market stall? About $40")
    model, results = scripted(
        call("get_item", {"description": "Folding table", "price_said": "About $40",
                          "reason": "Needed for the stall; not on the list, so it needs approval"}),
        call("get_item", {"description": "Folding table", "price_said": "About $40",
                          "reason": "for the market stall"}), done())
    ask(ActionAgent(model=model), session, "Marco", conversation=chat)
    assert results[0].status == "error" and "isn't something the member wrote" in results[0].content
    assert session.exec(select(Request)).one().reason == "for the market stall"


def test_act_sees_the_members_last_messages_word_for_word(session, group):
    """The coordinator's brief retold "We need it…" as "they need it…", and act's quote check refused it."""
    chat = said(session, "Marco", "hi", "Can I get a folding table?", "About $40", "We need it to show the flyers")
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages[0].content) or call(
        "ActResult", {"status": "cant_do"}))
    ask(ActionAgent(model=model), session, "Marco", "Marco says they need it to show the flyers", conversation=chat)
    assert seen[0].endswith('<member_said>\nMarco\'s last messages, oldest first, word for word:\n'
                            'Marco: "Can I get a folding table?"\nMarco: "About $40"\n'
                            'Marco: "We need it to show the flyers"\n</member_said>\n</chat_context>')


def test_adding_a_reason_instead_of_asking_twice(session, group):
    """The demo run asked for the folding table again to add a reason: the PICs saw it twice."""
    table = tell(session, "Marco", description="Folding table for the market stall", estimate_cents=40_00,
                 reason="for the market stall").request
    chat = said(session, "Marco", "We need it to show the flyers at the Saturday market")
    model, results = scripted(
        call("get_item", {"description": "Folding table",
                          "reason": "We need it to show the flyers at the Saturday market"}),
        call("add_reason", {"request_id": table.id,
                            "reason": "We need it to show the flyers at the Saturday market"}), done())
    result = ask(ActionAgent(model=model), session, "Marco", conversation=chat)
    assert results[0].status == "error" and f"Marco already asked for this: request {table.id}" in results[0].content
    assert json.loads(results[1].content) == {
        "type": "reason_added", "request_id": table.id, "item": "Folding table for the market stall",
        "reason": "for the market stall; We need it to show the flyers at the Saturday market"}
    assert [a.type for a in result.actions] == ["reason_added"]
    assert len(session.exec(select(Request)).all()) == 1
    told = session.exec(select(Notification).where(Notification.kind == "reason_added")).all()
    assert sorted(session.get(Member, n.member_id).name for n in told) == ["Dana", "Sam"]


def test_lookup_sees_what_the_buyer_answered(session, group):
    claim = past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    question = payouts.ask_buyer(session, claim.id, member(session, "Marco"), "Not in the supply box", now=NOW)
    payouts.answer_question(session, question.id, member(session, "Priya"), "On the shelf by the door")
    model, results = scripted(call("latest_purchase", {"item_id": item(session, "Gloves").id}), found())
    ask(LookupAgent(model=model), session, "Marco")
    assert json.loads(results[0].content)["answers"] == [
        {"asked_by": "Marco", "question": "Not in the supply box", "answer": "On the shelf by the door"}]
