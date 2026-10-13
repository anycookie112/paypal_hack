"""The group's memory: facts, patterns, conversations and the agent log."""

import json
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import memory
from app.core.payouts import answer_question, ask_buyer
from app.models import AgentLog, Conversation
from tests.demo import NOW, item, member, past_purchase, tell


def test_who_is_on_it_and_expiry(session, group):
    tell(session, "Priya", "Gloves")
    tell(session, "Marco", "Trash bags", now=NOW + timedelta(minutes=5))
    getting = memory.who_is_on_it(session, group, NOW + timedelta(minutes=10))
    assert [(o.member, o.item) for o in getting] == [("Priya", "Gloves"), ("Marco", "Trash bags")]
    assert memory.who_is_on_it(session, group, NOW + timedelta(hours=5)) == []  # both expired


def test_recent_purchases_newest_first_and_filtered(session, group):
    past_purchase(session, "Marco", "Gloves", 18_40, days_ago=9)
    past_purchase(session, "Priya", "Trash bags", 11_99, days_ago=2)
    assert [(p.member, p.item) for p in memory.recent_purchases(session, group)] == [
        ("Priya", "Trash bags"), ("Marco", "Gloves")]
    assert [p.total_cents for p in memory.recent_purchases(session, group, item=item(session, "Gloves"))] == [18_40]
    assert memory.recent_purchases(session, group, member=member(session, "Dana")) == []


def test_item_history_learns_how_long_things_last(session, group):
    for days_ago in (30, 20, 10):
        past_purchase(session, "Marco", "Gloves", 18_00, days_ago=days_ago)
    history = memory.item_history(session, item(session, "Gloves"))
    assert history.times_bought == 3 and history.last_buyer == "Marco"
    assert history.restock_days == 14 and history.observed_days == 10.0  # really lasts 10 days, not 14
    assert memory.item_history(session, item(session, "Coffee")).observed_days is None


def test_probably_needed_skips_fresh_items_and_items_someone_is_getting(session, group):
    assert memory.probably_needed(session, group, NOW) == []  # never bought: no evidence anything ran out
    for name in ("Gloves", "Tape and markers", "Snacks and water", "Coffee", "Flyer printing", "Cleaning wipes",
                 "First-aid restock"):
        past_purchase(session, "Dana", name, 5_00, days_ago=1)        # all fresh...
    past_purchase(session, "Dana", "Trash bags", 5_00, days_ago=20)   # ...but trash bags usually last 14 days
    assert memory.probably_needed(session, group, NOW) == ["Trash bags"]
    tell(session, "Marco", "Trash bags")
    assert memory.probably_needed(session, group, NOW) == []


def test_group_and_member_status(session, group):
    past_purchase(session, "Priya", "Coffee", 15_00, days_ago=3)
    tell(session, "Priya", "Gloves")                                         # $25 promised
    tell(session, "Priya", description="Folding table", estimate_cents=45_00)  # waits for an approver
    status = memory.group_status(session, group, NOW)
    assert status.overall.committed_cents == 40_00 and status.overall.left_cents == 260_00
    supplies = next(b for b in status.categories if b.name == "Supplies")
    assert (supplies.committed_cents, supplies.left_cents) == (25_00, 95_00)

    priya = memory.member_status(session, member(session, "Priya"), NOW)
    assert priya.committed_cents == 40_00 and priya.monthly_limit_cents == 100_00
    assert [(r.item, r.status) for r in priya.open_requests] == [("Gloves", "on_it"),
                                                                ("Folding table", "needs_approval")]


def test_conversations_keep_what_was_said_per_channel(session, group):
    priya = member(session, "Priya")
    web = memory.get_conversation(session, priya)
    assert memory.get_conversation(session, priya).id == web.id                      # same chat again
    assert memory.get_conversation(session, priya, channel="telegram", external_id="42").id != web.id
    memory.add_message(session, web, "user", "No gloves for this morning, I'm grabbing some")
    memory.add_message(session, web, "assistant", "Nobody's on it. Go ahead!",
                       cards=[{"type": "confirmed", "item": "Gloves", "cap_cents": 25_00}])
    memory.add_message(session, web, "user", "How much is left this month?")
    said = memory.recent_messages(session, web, limit=2)
    assert [m.role for m in said] == ["assistant", "user"]
    assert json.loads(said[0].cards_json)[0]["item"] == "Gloves"
    with pytest.raises(ValueError):
        memory.add_message(session, web, "tool", "nope")


def test_agent_log_records_steps(session, group):
    conversation = memory.get_conversation(session, member(session, "Priya"))
    memory.log_step(session, agent="group_desk", event="tool_call", conversation=conversation, name="who_is_on_it",
                    data={"item": "Gloves"}, prompt_id="group_desk@v1")
    entry = session.exec(select(AgentLog)).one()
    assert (entry.event, entry.name, json.loads(entry.data_json)) == ("tool_call", "who_is_on_it", {"item": "Gloves"})
    assert session.exec(select(Conversation)).one().id == entry.conversation_id


def test_finding_items_by_any_of_their_names(session, group):
    assert [i.name for i in memory.find_items(session, group, "work gloves")] == ["Gloves"]
    assert memory.find_items(session, group, "bin bags")[0].name == "Trash bags"
    assert memory.find_items(session, group, "oat milk") == []
    assert len(memory.find_items(session, group, "")) == 8  # the whole list
    gloves = memory.find_items(session, group, "gloves")[0]
    assert (gloves.cap_cents, gloves.restock_days, gloves.proof, gloves.category) == (
        25_00, 14, "receipt_photo", "Supplies")


def test_questions_about_my_purchases(session, group):
    claim = past_purchase(session, "Priya", "Gloves", 18_40, days_ago=1)
    ask_buyer(session, claim.id, member(session, "Marco"), "Not in the supply box", now=NOW)
    [question] = memory.questions_for(session, member(session, "Priya"))
    assert (question.item, question.asked_by, question.text) == ("Gloves", "Marco", "Not in the supply box")
    answer_question(session, question.question_id, member(session, "Priya"), "In the van")
    assert memory.questions_for(session, member(session, "Priya")) == []
