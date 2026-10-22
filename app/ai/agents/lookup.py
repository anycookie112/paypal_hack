"""The lookup agent: answers the coordinator's questions from the group's memory. Read tools only (docs/SPEC.md §8.2).

Each tool returns a typed record, and the agent finishes with `LookupResult`: the records that answer the request,
copied exactly, checked by code against what the tools returned before the coordinator sees them.
"""

from typing import Literal

from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlmodel import select

from app.ai.agents.context import Chat, ChatAgent, Record, not_returned, returned, when
from app.ai.base import ToolResult
from app.core import memory, settings
from app.core.requests import ActionError, admins, approvers
from app.domain.money import cents_to_str
from app.models import Item


# --- what the tools return ------------------------------------------------------------------------

class PurchaseAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asked_by: str
    question: str
    answer: str  # the buyer's answer, e.g. where they put it


class Purchase(Record):
    """latest_purchase: who was in charge of the last purchase of an item."""
    type: Literal["purchase"] = "purchase"
    claim_id: int
    item_id: int
    item: str
    buyer: str
    bought: str
    store: str
    total: str | None    # None: the group keeps amounts private
    has_photo: bool      # a photo of where it was put
    claim_status: str    # review | waiting (in its hold) | held | paying | paid | ...
    payout_due: str | None
    open_questions: int
    open_reports: int
    answers: list[PurchaseAnswer]  # questions about it the buyer has answered


class NeverBought(Record):
    type: Literal["never_bought"] = "never_bought"
    item_id: int
    item: str


class History(Record):
    """item_history: how often an item is bought."""
    type: Literal["item_history"] = "item_history"
    item_id: int
    item: str
    restock_days: int                    # what the treasurer set
    times_bought: int
    last_bought: str | None
    last_buyer: str | None
    average_days_between: float | None   # once it's been bought at least twice


class BudgetLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    budget: str | None   # None: no budget set
    committed: str       # spent this month + confirmed but not bought yet
    left: str | None


class Budgets(Record):
    type: Literal["budgets"] = "budgets"
    overall: BudgetLine
    categories: list[BudgetLine]


class Rules(Record):
    type: Literal["rules"] = "rules"
    settings: list[str]
    pics: list[str]
    treasurer: list[str]


Fact = Purchase | NeverBought | History | Budgets | Rules
_FACTS = TypeAdapter(Fact)


class LookupResult(BaseModel):
    """Finish by calling this: it's the only thing the coordinator sees."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "found",
        "facts": [{"type": "never_bought", "item_id": 3, "item": "Coffee"}],
        "closest_item_ids": []}]})

    status: Literal["found", "not_found"] = Field(
        description="found: tools returned facts that answer the request; not_found: they didn't (nothing matches, "
                    "or you can't tell which item is meant: then list the closest in closest_item_ids).")
    facts: list[Fact] = Field(default_factory=list, description="The results your tools returned that answer the "
                                                                "request, each copied exactly: same fields, same "
                                                                "values.")
    closest_item_ids: list[int] = Field(default_factory=list, description="For not_found: the item_ids from <items> "
                                                                          "closest to what was asked about.")

    def check(self, results: list[ToolResult], chat: Chat) -> list[str]:
        """What's wrong with this result, given what the tools returned in this run (empty: it's right)."""
        problems = not_returned("facts", self.facts, returned(results, _FACTS))
        if self.status == "found" and not self.facts:
            problems.append("status is found, but facts is empty")
        if self.status == "not_found" and self.facts:
            problems.append("status is not_found, but there are facts: use found")
        items = set(chat.session.exec(select(Item.id).where(Item.group_id == chat.group.id, Item.active)).all())
        problems += [f"closest_item_ids: {i} isn't an item in <items>" for i in self.closest_item_ids
                     if i not in items]
        return problems


class LookupAgent(ChatAgent):
    name = "lookup"
    description = ("Use this for facts that aren't already in <chat_context>: who last bought something, where they "
                   "put it and whether its payout is on hold (with the claim_id, for \"I can't find X\"); how often "
                   "an item is bought; this month's budgets for the group and each category; the group's rules, and "
                   "who the PICs and the treasurer are. Not for which item someone means, who's getting what, what's "
                   "running low, or the member's own requests, limit, payouts and questions: <items> and <member> "
                   "already say. Returns the records' facts as checked fields, with their ids.")
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
        item = _item(chat, item_id)
        details = memory.latest_purchase(chat.session, item)
        if details is None:
            return NeverBought(item_id=item.id, item=item.name).model_dump_json()
        private = details.member != chat.member.name and not settings.get_all(chat.session, chat.group)[
            "feed_shows_amounts"]  # the group keeps amounts private
        return Purchase(
            claim_id=details.claim_id, item_id=item.id, item=details.item, buyer=details.member,
            bought=when(details.when), store=details.store, total=None if private else cents_to_str(details.total_cents),
            has_photo=details.has_photo, claim_status=details.status,
            payout_due=when(details.payout_due_at) if details.payout_due_at else None,
            open_questions=details.open_questions, open_reports=details.open_reports,
            answers=[PurchaseAnswer(asked_by=a.asked_by, question=a.question, answer=a.answer)
                     for a in details.answers]).model_dump_json()

    def item_history(self, item_id: int, runtime: ToolRuntime[Chat]) -> str:
        """How often an item is bought: times bought, last bought and by whom, the usual restock time, and the
        average days between purchases so far.

        Args:
            item_id: The item, from the item list.
        """
        chat = runtime.context
        item = _item(chat, item_id)
        history = memory.item_history(chat.session, item)
        return History(
            item_id=item.id, item=history.item, restock_days=history.restock_days, times_bought=history.times_bought,
            last_bought=when(history.last_bought) if history.last_bought else None, last_buyer=history.last_buyer,
            average_days_between=history.observed_days).model_dump_json()

    def budgets(self, runtime: ToolRuntime[Chat], category: str = "") -> str:
        """This month's budget for the group and its categories: the budget, what's spent or promised, what's left.

        Args:
            category: One category's name, when only that one is needed; empty for every category. The group's
                overall budget always comes back too.
        """
        chat = runtime.context
        status = memory.group_status(chat.session, chat.group, chat.time)
        lines = status.categories
        if category.strip():  # a live run dropped the categories it wasn't asked about, and the copy check refused it
            lines = [b for b in lines if b.name.lower() == category.strip().lower()]
            if not lines:
                raise ActionError(f"No category called \"{category}\". The categories: "
                                  f"{', '.join(b.name for b in status.categories)}.")
        return Budgets(overall=_line(status.overall), categories=[_line(b) for b in lines]).model_dump_json()

    def group_rules(self, runtime: ToolRuntime[Chat]) -> str:
        """The group's settings in plain words, and who the PICs (approvers) and the treasurer are."""
        chat = runtime.context
        values = settings.get_all(chat.session, chat.group)
        return Rules(settings=[settings.describe(key, value) for key, value in values.items()],
                     pics=[m.name for m in approvers(chat.session, chat.group)],
                     treasurer=[m.name for m in admins(chat.session, chat.group)]).model_dump_json()


def _item(chat: Chat, item_id: int) -> Item:
    item = chat.session.exec(select(Item).where(Item.id == item_id, Item.group_id == chat.group.id)).first()
    if item is None:
        raise ActionError(f"No item with id {item_id}: check the item list for its id.")
    return item


def _line(budget: memory.Budget) -> BudgetLine:
    left = budget.left_cents
    return BudgetLine(name=budget.name, budget=cents_to_str(budget.budget_cents) if budget.budget_cents else None,
                      committed=cents_to_str(budget.committed_cents),
                      left=cents_to_str(left) if left is not None else None)
