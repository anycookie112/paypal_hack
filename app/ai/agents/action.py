"""The action agent: does what the coordinator asks, as the member chatting. Action tools only (docs/SPEC.md §8.3).

Every tool calls a core function as `chat.member`, so the AI can't act for anyone else, and every rule and permission
is checked by code. Approving, resolving reports and paying are never tools.
"""

from datetime import datetime
from typing import Literal

from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field

from app.ai.agents.context import Chat, ChatAgent, member_said, when
from app.core import payouts
from app.core.members import set_paypal_email
from app.core.requests import ActionError, change_time, release_request, submit_request, thing
from app.core.times import deadline_from
from app.domain.money import cents_to_str, to_cents
from app.models import Member


class ActionResult(BaseModel):
    """Finish by calling this: it's the only thing the coordinator sees."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "done",
        "message": "Confirmed (request 4): Priya is getting Gloves, paid back up to $25.00, by Sat 17 Oct, 13:00 "
                   "UTC. The group can see it.",
        "missing": []}]})

    status: Literal["done", "needs_info", "refused"] = Field(
        description="done: a tool succeeded (also 'sent to a PIC to approve'); needs_info: something needed is "
                    "missing, so no tool was called; refused: a tool said no (someone else is getting it, a rule, a "
                    "permission).")
    message: str = Field(description="What happened or why not, in the tool's own words.")
    missing: list[str] = Field(default_factory=list, description="For needs_info: what to ask the member, "
                                                             "e.g. 'when they'll get it'.")


class ActionAgent(ChatAgent):
    name = "act"
    description = ("Use this to change something for the member, once you have every detail it needs: get an item "
                   "(the item_id, plus the member's own words for when if they said; for something not on the "
                   "list, a description and a reason), change the time on one of their requests (their own words "
                   "for the new time), release one of their own requests, ask the person who bought something where it is (claim_id "
                   "from lookup), answer a question about their own purchase, file a formal report (only after they "
                   "said yes to it), or set their PayPal email. Not for looking things up, and not for asking the "
                   "member anything: ask them yourself first. Returns done, needs_info (what to ask them) or "
                   "refused (why), in the app's own words.")
    prompt = "action"
    output = ActionResult

    def setup(self):
        for tool in (self.get_item, self.change_time, self.release, self.ask_buyer, self.answer_question,
                     self.report_problem, self.set_paypal_email):
            self.register_tool(tool)

    def get_item(self, runtime: ToolRuntime[Chat], item_id: int | None = None, description: str = "",
                 when_said: str = "", estimate: str = "", quantity: int = 1, reason: str = "",
                 insist: bool = False) -> str:
        """Tell the group the member is getting something. The app's rules decide: confirmed (it's theirs, up to the
        item's cap), sent to a PIC to approve, declined, or someone is already getting it.

        Args:
            item_id: The common item's id, from the item list. Leave empty for something not on the list.
            description: What it is, for something not on the list.
            when_said: The member's own words for when they'll get it, copied exactly from what they wrote
                ("by 11", "in 2 hours"). The app works out the time. Empty if they didn't say: they get the group's
                default time and can change it later.
            estimate: Roughly what it will cost, e.g. "18.50", if they said.
            quantity: How many.
            reason: Why it's needed: required for anything not on the list or over a limit.
            insist: True only if they still want it after hearing someone else is already getting it.
        """
        chat = runtime.context
        try:
            estimate_cents = to_cents(estimate) if estimate.strip() else None
        except ValueError as exc:
            raise ActionError(str(exc))
        until = _deadline(chat, when_said) if when_said.strip() else None
        result = submit_request(chat.session, chat.member, item_id=item_id, description=description,
                                quantity=quantity, estimate_cents=estimate_cents, reason=reason, insist=insist,
                                until=until, now=chat.now)
        decision, request = result.decision, result.request
        if decision.outcome == "already_on_it":
            others = "; ".join(f"{chat.session.get(Member, r.member_id).name} is getting it (until "
                               f"{when(r.on_it_until)})" for r in decision.on_it)
            return f"Not recorded: {others}. If they still want it too, ask again with insist (a PIC decides)."
        what = thing(chat.session, request)
        if decision.outcome == "confirmed":
            default = "" if until else " (the group's usual time: they didn't say when, and can change it)"
            return (f"Confirmed (request {request.id}): {chat.member.name} is getting {what}, paid back up to "
                    f"{cents_to_str(request.cap_cents)}, by {when(request.on_it_until)}{default}. The group can see "
                    "it.")
        if decision.outcome == "needs_approval":
            return (f"Sent to a PIC to approve (request {request.id}): {' '.join(decision.reasons)} "
                    f"Reason given: {request.reason or 'none'}.")
        return f"Declined: {' '.join(decision.reasons)}"

    def change_time(self, request_id: int, when_said: str, runtime: ToolRuntime[Chat]) -> str:
        """Change how long the member has to get something they're already getting. Only their own requests.

        Args:
            request_id: Their request, from their open requests in <member>.
            when_said: The member's own words for the new time, copied exactly from what they wrote ("by 11").
        """
        chat = runtime.context
        request = change_time(chat.session, request_id, chat.member, _deadline(chat, when_said), now=chat.now)
        return (f"Changed (request {request.id}): {chat.member.name} now has until {when(request.on_it_until)} to get "
                f"{thing(chat.session, request)}.")

    def release(self, request_id: int, runtime: ToolRuntime[Chat]) -> str:
        """The member can't get it after all: the item is free for someone else. Only their own requests.

        Args:
            request_id: Their request, from their open requests in <member>.
        """
        chat = runtime.context
        request = release_request(chat.session, request_id, chat.member)
        return f"Released request {request.id}: {thing(chat.session, request)} is free for someone else."

    def ask_buyer(self, claim_id: int, question: str, runtime: ToolRuntime[Chat]) -> str:
        """Step 1 when something's missing: ask the person who bought it where it is. Nothing else happens: no hold
        on their payout, nothing to the treasurer.

        Args:
            claim_id: The purchase, from the lookup.
            question: What the member wants to know, e.g. "The gloves aren't in the supply box".
        """
        chat = runtime.context
        asked = payouts.ask_buyer(chat.session, claim_id, chat.member, question, now=chat.now)
        return f"Asked (question {asked.id}). Their answer will come back to {chat.member.name}."

    def answer_question(self, question_id: int, answer: str, runtime: ToolRuntime[Chat]) -> str:
        """Answer a question someone asked about the member's own purchase.

        Args:
            question_id: The question, from the questions in <member>.
            answer: Where it is, in the member's words.
        """
        chat = runtime.context
        payouts.answer_question(chat.session, question_id, chat.member, answer)
        return "Answered: whoever asked has been told."

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
        return f"Report {report.id} filed: the treasurer will look into it privately."

    def set_paypal_email(self, email: str, runtime: ToolRuntime[Chat]) -> str:
        """Set where the member's payouts go.

        Args:
            email: Their PayPal email address.
        """
        chat = runtime.context
        set_paypal_email(chat.session, chat.member, email)
        return f"Payouts will go to {chat.member.paypal_email}."


def _deadline(chat: Chat, when_said: str) -> datetime:
    """The deadline in the member's own words, checked against what they actually wrote: the model can't pass a time
    the member never gave (a live test had it invent "10 hours")."""
    if " ".join(when_said.lower().split()).strip(" .!,\"'") not in member_said(chat):
        raise ActionError(f"\"{when_said}\" isn't something the member wrote. Copy their exact words for when, or "
                          "leave it empty if they didn't say.")
    return deadline_from(when_said, chat.time)
