"""The lookup agent: answers the coordinator's questions from the group's memory. Read tools only (docs/SPEC.md §8.2)."""

from typing import Literal

from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from app.ai.agents.context import Chat, ChatAgent, facts
from app.core import memory, settings
from app.core.requests import ActionError, admins, approvers
from app.models import Item


class LookupResult(BaseModel):
    """Finish by calling this: it's the only thing the coordinator sees."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "found",
        "answer": "Gloves (item_id 1) were last bought 9 days ago by Marco at Riverside Hardware. There's a photo of "
                  "where he put them; the payout is still on hold."}]})

    status: Literal["found", "not_found"] = Field(
        description="found: the tools answered the request; not_found: they didn't (nothing matches, or you can't "
                    "tell which item is meant: then name the closest ones in the answer).")
    answer: str = Field(description="The facts, plainly: who, what, when, how much, with every id the "
                                    "coordinator needs (item_id, request_id, claim_id, question_id).")


class LookupAgent(ChatAgent):
    name = "lookup"
    description = ("Use this for the group's records beyond the item list: who bought something last, where it was "
                   "put and whether its payout is on hold; how often an item is bought; budgets; the member's own "
                   "status (their requests, claims, payouts, questions for them); the group's rules. Returns the "
                   "facts with their ids.")
    prompt = "lookup"
    output = LookupResult

    def setup(self):
        # Which item, who's getting it and what's running low are in the item list in the prompt (ChatAgent).
        for tool in (self.latest_purchase, self.item_history, self.budgets, self.my_status, self.group_rules):
            self.register_tool(tool)

    def latest_purchase(self, item_id: int, runtime: ToolRuntime[Chat]) -> str:
        """The last time an item was bought: who got it, when, the store, whether there's a photo of where it was
        put, and whether its payout is still on hold. For "I can't find the gloves".

        Args:
            item_id: The item, from the item list.
        """
        chat = runtime.context
        details = memory.latest_purchase(chat.session, _item(chat, item_id))
        if details is None:
            return "It hasn't been bought yet."
        if details.member != chat.member.name and not settings.get_all(chat.session, chat.group)["feed_shows_amounts"]:
            details.total_cents = None  # the group keeps amounts private
        return facts(details)

    def item_history(self, item_id: int, runtime: ToolRuntime[Chat]) -> str:
        """How often an item is bought: times bought, last bought and by whom, the usual restock time, and the
        average days between purchases so far.

        Args:
            item_id: The item, from the item list.
        """
        chat = runtime.context
        return facts(memory.item_history(chat.session, _item(chat, item_id)))

    def budgets(self, runtime: ToolRuntime[Chat]) -> str:
        """This month's budget for the group and each category: the budget, what's spent or promised, what's left."""
        chat = runtime.context
        return facts(memory.group_status(chat.session, chat.group, chat.time))

    def my_status(self, runtime: ToolRuntime[Chat]) -> str:
        """The member's own status: their monthly limit and what they've used, their open requests (with ids),
        claims waiting for payout, questions others asked about their purchases (with ids), and whether their
        PayPal email is set."""
        chat = runtime.context
        return facts({"status": memory.member_status(chat.session, chat.member, chat.time),
                      "questions_for_me": memory.questions_for(chat.session, chat.member),
                      "paypal_email_set": bool(chat.member.paypal_email)})

    def group_rules(self, runtime: ToolRuntime[Chat]) -> str:
        """The group's settings in plain words, and who the PICs (approvers) and the treasurer are."""
        chat = runtime.context
        values = settings.get_all(chat.session, chat.group)
        return facts({"settings": [settings.describe(key, value) for key, value in values.items()],
                      "pics": [m.name for m in approvers(chat.session, chat.group)],
                      "treasurer": [m.name for m in admins(chat.session, chat.group)]})


def _item(chat: Chat, item_id: int) -> Item:
    item = chat.session.exec(select(Item).where(Item.id == item_id, Item.group_id == chat.group.id)).first()
    if item is None:
        raise ActionError(f"No item with id {item_id}: check the item list for its id.")
    return item
