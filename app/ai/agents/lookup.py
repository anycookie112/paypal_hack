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
    description = ("Use this for facts that aren't already in <chat_context>: who last bought something, where they "
                   "put it and whether its payout is on hold (with the claim_id, for \"I can't find X\"); how often "
                   "an item is bought; this month's budgets for the group and each category; the group's rules, and "
                   "who the PICs and the treasurer are. Not for which item someone means, who's getting what, what's "
                   "running low, or the member's own requests, limit, payouts and questions: <items> and <member> "
                   "already say. Returns the facts with their ids.")
    prompt = "lookup"
    output = LookupResult

    def setup(self):
        # Which item, who's getting it, what's running low and the member's own status are in the prompt
        # (<items> and <member>, from ChatAgent), so they aren't tools.
        for tool in (self.latest_purchase, self.item_history, self.budgets, self.group_rules):
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
