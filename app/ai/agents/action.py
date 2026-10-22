"""The action agent: does what the coordinator asks, as the member chatting. Action tools only (docs/SPEC.md §8.3).

Every tool calls a core function as `chat.member`, so the AI can't act for anyone else, and every rule and permission
is checked by code. Approving, resolving reports and paying are never tools.

Each tool returns a typed record (what happened, as JSON), and the agent finishes with `ActResult`: those records
copied exactly, checked by code against what the tools returned before the coordinator sees them.
"""

from datetime import datetime
from typing import Literal

from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlmodel import Session

from app.ai.agents.context import Chat, ChatAgent, Record, last_said, member_said, not_returned, returned, when
from app.ai.base import ToolResult
from app.core import payouts
from app.core.members import set_paypal_email
from app.core.requests import ActionError, add_reason, change_time, release_request, submit_request, thing
from app.core.prices import price_range
from app.core.times import deadline_from
from app.domain.money import cents_to_str, range_to_str
from app.models import Claim, Item, Member


# --- what the tools return ------------------------------------------------------------------------

class OnItBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member: str
    until: str
    request_id: int


class ItemRequested(Record):
    """get_item: what the app's rules decided."""
    type: Literal["item_requested"] = "item_requested"
    outcome: Literal["confirmed", "needs_approval", "declined", "already_on_it"]
    request_id: int | None      # None for already_on_it: nothing was recorded
    item: str
    paid_back_up_to: str | None  # confirmed: the most they're paid back
    until: str | None            # confirmed: when they have to get it by
    usual_time: bool             # they didn't say when: the group's usual time, which they can change
    reasons: list[str]           # needs_approval or declined: why, in the rules' words
    price: str | None            # the member's price range, worked out by code from their words
    reason_given: str | None     # the reason passed to get_item
    already_getting_it: list[OnItBy]  # already_on_it: who


class TimeChanged(Record):
    type: Literal["time_changed"] = "time_changed"
    request_id: int
    item: str
    until: str


class ReasonAdded(Record):
    type: Literal["reason_added"] = "reason_added"
    request_id: int
    item: str
    reason: str  # the whole reason the PICs now see


class Released(Record):
    type: Literal["released"] = "released"
    request_id: int
    item: str


class Asked(Record):
    type: Literal["asked"] = "asked"
    question_id: int
    claim_id: int
    asked: str  # who was asked: the person who bought it


class Answered(Record):
    type: Literal["answered"] = "answered"
    question_id: int
    told: str  # who asked, and has now been told


class Reported(Record):
    type: Literal["reported"] = "reported"
    report_id: int
    claim_id: int


class PaypalEmailSet(Record):
    type: Literal["paypal_email_set"] = "paypal_email_set"
    email: str


Action = ItemRequested | TimeChanged | ReasonAdded | Released | Asked | Answered | Reported | PaypalEmailSet
_ACTIONS = TypeAdapter(Action)

# What the coordinator can be told to ask the member: a fixed list, so it asks for exactly that.
Missing = Literal["what they're getting", "a price range", "why it's needed", "a clock time", "which item", "which request",
                  "which purchase", "what's wrong", "what they checked", "their yes to a formal report",
                  "their answer", "their PayPal email"]


class ActResult(BaseModel):
    """Finish by calling this: it's the only thing the coordinator sees."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "done",
        "actions": [{"type": "time_changed", "request_id": 4, "item": "Gloves", "until": "Sat 17 Oct, 13:00 UTC"}],
        "missing": [], "refusal": None}]})

    status: Literal["done", "needs_info", "refused", "cant_do"] = Field(
        description="done: a tool ran and gave its result (read each action's outcome); needs_info: something "
                    "needed is missing (list it in missing); refused: a tool said no (copy what it said into "
                    "refusal); cant_do: none of your tools does what the request asks (call no tool).")
    actions: list[Action] = Field(
        default_factory=list, description="Every result a tool returned in this run (not refusals), copied exactly: "
                                          "same fields, same values.")
    missing: list[Missing] = Field(default_factory=list, description="For needs_info: what to ask the member.")
    refusal: str | None = Field(default=None, description="For refused: the tool's message, copied exactly.")

    def check(self, results: list[ToolResult], chat: Chat) -> list[str]:
        """What's wrong with this result, given what the tools returned in this run (empty: it's right)."""
        seen = returned(results, _ACTIONS)
        problems = not_returned("actions", self.actions, seen)
        problems += [f"actions is missing a result a tool returned: {r.model_dump_json()}" for r in seen
                     if r not in self.actions]
        refusals = [r.content for r in results if not r.ok]
        if self.status == "done" and not self.actions:
            problems.append("status is done, but no tool returned a result")
        if self.status == "refused" and self.refusal not in refusals:
            problems.append("refusal must be one tool's message, copied exactly"
                            + (f": one of {refusals}" if refusals else ", and no tool said no in this run"))
        if self.status != "refused" and self.refusal is not None:
            problems.append("refusal is only for status refused")
        if self.status == "needs_info" and not self.missing:
            problems.append("status is needs_info, but missing is empty")
        if self.status != "needs_info" and self.missing:
            problems.append("missing is only for status needs_info")
        if self.status == "cant_do" and self.actions:
            problems.append("status is cant_do, but a tool did something: use done")
        return problems


class ActionAgent(ChatAgent):
    name = "act"
    description = ("Use this to change something for the member, once you have every detail it needs: get an item "
                   "(the item_id, plus the member's own words for when if they said; for something not on the "
                   "list, a description and a reason), change the time on one of their requests (their own words "
                   "for the new time), add their reason to one of their requests waiting for a PIC, release one of their own requests, ask the person who bought something where it is (claim_id "
                   "from lookup), answer a question about their own purchase, file a formal report (only after they "
                   "said yes to it), or set their PayPal email. Not for looking things up, and not for asking the "
                   "member anything: ask them yourself first. Returns a status (done, needs_info, refused, cant_do) "
                   "and what the app recorded, as checked fields.")
    prompt = "action"
    output = ActResult

    def setup(self):
        for tool in (self.get_item, self.change_time, self.add_reason, self.release, self.ask_buyer, self.answer_question,
                     self.report_problem, self.set_paypal_email):
            self.register_tool(tool)

    def instructions(self, chat: Chat) -> str:
        """<chat_context>, plus the member's last messages word for word (<member_said>): reasons and times must be
        their own words, and the brief may retell them (5 of 10 reliability runs had act refused 2–4 times)."""
        return "\n\n".join(part for part in (super().instructions(chat), last_said(chat)) if part)

    def get_item(self, runtime: ToolRuntime[Chat], item_id: int | None = None, description: str = "",
                 when_said: str = "", price_said: str = "", quantity: int = 1, reason: str = "",
                 insist: bool = False) -> str:
        """Tell the group the member is getting something. The app's rules decide the outcome: confirmed (it's
        theirs, up to the item's cap), needs_approval (sent to a PIC), declined, or already_on_it (someone else is
        getting it: nothing is recorded; if the member still wants it too, call again with insist and a PIC decides).

        Args:
            item_id: The common item's id, from the item list. Leave empty for something not on the list.
            description: What it is, for something not on the list.
            when_said: The member's own words for when they'll get it, copied exactly from what they wrote
                ("by 11", "in 2 hours"). The app works out the time. Empty if they didn't say: they get the group's
                default time and can change it later.
            price_said: The member's own words for what it will cost, copied exactly from what they wrote; a
                rough range is fine. The app works out the range. Required for anything not on the list; empty if
                they didn't say.
            quantity: How many.
            reason: Why it's needed, copied exactly from what the member wrote: required for anything not on the
                list or over a limit. Empty if they didn't say.
            insist: True only if they still want it after hearing someone else is already getting it.
        """
        chat = runtime.context
        low = high = None
        if price_said.strip():
            _quoted(chat, price_said, "the price")
            low, high = price_range(price_said)
        until = _deadline(chat, when_said) if when_said.strip() else None
        _quoted(chat, reason, "reason")
        result = submit_request(chat.session, chat.member, item_id=item_id, description=description,
                                quantity=quantity, estimate_cents=high, estimate_low_cents=low, reason=reason,
                                insist=insist, until=until, now=chat.now)
        decision, request = result.decision, result.request
        if decision.outcome == "already_on_it":
            return ItemRequested(
                outcome="already_on_it", request_id=None, item=_named(chat, item_id, description),
                paid_back_up_to=None, until=None, usual_time=False, reasons=[], price=None,
                reason_given=reason.strip() or None,
                already_getting_it=[OnItBy(member=chat.session.get(Member, r.member_id).name, until=when(r.on_it_until),
                                           request_id=r.id) for r in decision.on_it]).model_dump_json()
        confirmed = decision.outcome == "confirmed"
        return ItemRequested(
            outcome=decision.outcome, request_id=request.id, item=thing(chat.session, request),
            paid_back_up_to=cents_to_str(request.cap_cents) if confirmed else None,
            until=when(request.on_it_until) if confirmed else None, usual_time=confirmed and until is None,
            reasons=[] if confirmed else list(decision.reasons),
            price=range_to_str(request.estimate_low_cents, request.estimate_cents) if request.estimate_cents else None,
            reason_given=request.reason or None,
            already_getting_it=[]).model_dump_json()

    def change_time(self, request_id: int, when_said: str, runtime: ToolRuntime[Chat]) -> str:
        """Change how long the member has to get something they're already getting. Only their own requests.

        Args:
            request_id: Their request, from their open requests in <member>.
            when_said: The member's own words for the new time, copied exactly from what they wrote ("by 11").
        """
        chat = runtime.context
        request = change_time(chat.session, request_id, chat.member, _deadline(chat, when_said), now=chat.now)
        return TimeChanged(request_id=request.id, item=thing(chat.session, request),
                           until=when(request.on_it_until)).model_dump_json()

    def add_reason(self, request_id: int, reason: str, runtime: ToolRuntime[Chat]) -> str:
        """Add why it's needed to one of the member's own requests that's waiting for a PIC: the PICs are told.

        Args:
            request_id: Their request, from their open requests in <member>.
            reason: Why it's needed, copied exactly from what the member wrote.
        """
        chat = runtime.context
        _quoted(chat, reason, "reason")
        request = add_reason(chat.session, request_id, chat.member, reason)
        return ReasonAdded(request_id=request.id, item=thing(chat.session, request),
                           reason=request.reason).model_dump_json()

    def release(self, request_id: int, runtime: ToolRuntime[Chat]) -> str:
        """The member can't get it after all: the item is free for someone else. Only their own requests.

        Args:
            request_id: Their request, from their open requests in <member>.
        """
        chat = runtime.context
        request = release_request(chat.session, request_id, chat.member)
        return Released(request_id=request.id, item=thing(chat.session, request)).model_dump_json()

    def ask_buyer(self, claim_id: int, question: str, runtime: ToolRuntime[Chat]) -> str:
        """Step 1 when something's missing: ask the person who bought it where it is. Nothing else happens: no hold
        on their payout, nothing to the treasurer.

        Args:
            claim_id: The purchase, from the lookup.
            question: What the member wants to know, e.g. "The gloves aren't in the supply box".
        """
        chat = runtime.context
        asked = payouts.ask_buyer(chat.session, claim_id, chat.member, question, now=chat.now)
        return Asked(question_id=asked.id, claim_id=claim_id,
                     asked=_buyer(chat.session, claim_id)).model_dump_json()

    def answer_question(self, question_id: int, answer: str, runtime: ToolRuntime[Chat]) -> str:
        """Answer a question someone asked about the member's own purchase.

        Args:
            question_id: The question, from the questions in <member>.
            answer: Where it is, in the member's words.
        """
        chat = runtime.context
        question = payouts.answer_question(chat.session, question_id, chat.member, answer)
        return Answered(question_id=question.id,
                        told=chat.session.get(Member, question.member_id).name).model_dump_json()

    def report_problem(self, claim_id: int, what_is_wrong: str, checked: str, runtime: ToolRuntime[Chat]) -> str:
        """Step 2, a formal report, only once asking the buyer didn't sort it out and the member confirmed they
        want to file it. It goes privately to the treasurer and holds the payout longer.

        Args:
            claim_id: The purchase, from the lookup.
            what_is_wrong: What's wrong, in the member's words.
            checked: What they checked first, e.g. "the supply box; asked Priya, no answer".
        """
        chat = runtime.context
        report = payouts.report_problem(chat.session, claim_id, chat.member, what_is_wrong, checked=checked,
                                        now=chat.now)
        return Reported(report_id=report.id, claim_id=claim_id).model_dump_json()

    def set_paypal_email(self, email: str, runtime: ToolRuntime[Chat]) -> str:
        """Set where the member's payouts go.

        Args:
            email: Their PayPal email address.
        """
        chat = runtime.context
        set_paypal_email(chat.session, chat.member, email)
        return PaypalEmailSet(email=chat.member.paypal_email).model_dump_json()


def _deadline(chat: Chat, when_said: str) -> datetime:
    """The deadline in the member's own words, checked against what they actually wrote: the model can't pass a time
    the member never gave (a live test had it invent "10 hours")."""
    _quoted(chat, when_said, "when")
    return deadline_from(when_said, chat.time)


def _quoted(chat: Chat, words: str, what: str) -> None:
    """`words` must be the member's own, copied from what they wrote: a live run had act write its own reason
    ("not on the item list, so it needs a reason and approval")."""
    if words.strip() and " ".join(words.lower().split()).strip(" .!?,\"'") not in member_said(chat):
        raise ActionError(f"\"{words}\" isn't something the member wrote. Copy their exact words for {what}, or "
                          "leave it empty if they didn't say.")


def _named(chat: Chat, item_id: int | None, description: str) -> str:
    item = chat.session.get(Item, item_id) if item_id is not None else None
    return item.name if item else description.strip()


def _buyer(session: Session, claim_id: int) -> str:
    return session.get(Member, session.get(Claim, claim_id).member_id).name
