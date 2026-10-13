"""The lookup agent: answers the coordinator's questions from the group's memory. Read tools only (docs/SPEC.md §8.2)."""

from typing import Literal

from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field
from sqlmodel import select

from app.ai.agents.context import Chat, facts, when
from app.ai.base import BaseAgent
from app.core import memory, settings
from app.core.requests import ActionError, admins, approvers
from app.models import Item


class LookupResult(BaseModel):
    """Finish by calling this: it's the only thing the coordinator sees."""
    status: Literal["found", "not_found"] = Field(
        description="found: the tools answered the request; not_found: they didn't (nothing matches, or you "
                    "can't tell which item is meant).")
    answer: str = Field(description="The facts, plainly: who, what, when, how much, with every id the "
                                    "coordinator needs (item_id, request_id, claim_id, question_id).")


class LookupAgent(BaseAgent):
    name = "lookup"
    description = ("Look things up: which list item someone means, who's getting what, who bought something and when, "
                   "what's running low, budgets, the member's own status and the group's rules.")
    prompt = "lookup"
    context = Chat
    output = LookupResult

    def setup(self):
        for tool in (self.find_items, self.who_is_on_it, self.latest_purchase, self.item_history,
                     self.probably_needed, self.budgets, self.my_status, self.group_rules):
            self.register_tool(tool)

    def instructions(self, chat: Chat) -> str:
        return f"The member chatting is {chat.member.name} ({chat.member.role}). It's {when(chat.time)}."

    def find_items(self, query: str, runtime: ToolRuntime[Chat]) -> str:
        """Common items matching what someone said, best match first: id, name, other names, the most paid back
        (cap), how long it usually lasts, the proof needed. An empty query lists every item.

        Args:
            query: The thing, in the member's words, e.g. "work gloves".
        """
        chat = runtime.context
        return facts(memory.find_items(chat.session, chat.group, query))

    def who_is_on_it(self, runtime: ToolRuntime[Chat]) -> str:
        """Everyone getting something right now, and until when."""
        chat = runtime.context
        return facts(memory.who_is_on_it(chat.session, chat.group, chat.time))

    def latest_purchase(self, item_id: int, runtime: ToolRuntime[Chat]) -> str:
        """The last time an item was bought: who got it, when, the store, whether there's a photo of where it was
        put, and whether its payout is still on hold. For "I can't find the gloves".

        Args:
            item_id: The item, from find_items.
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
            item_id: The item, from find_items.
        """
        chat = runtime.context
        return facts(memory.item_history(chat.session, _item(chat, item_id)))

    def probably_needed(self, runtime: ToolRuntime[Chat]) -> str:
        """Common items that have probably run out (past their usual restock time) that nobody is getting."""
        chat = runtime.context
        return facts(memory.probably_needed(chat.session, chat.group, chat.time))

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
        raise ActionError(f"No item with id {item_id}: use find_items to get the id.")
    return item
