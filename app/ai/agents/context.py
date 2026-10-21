"""What every agent run gets: who's chatting. Tools act as this member and read through this session.

`ChatAgent` is the base of the group fund's agents: it runs with a `Chat` and logs every tool call and result to the
agent log (docs/SPEC.md §14.2), so "why did the bot say that?" can always be answered.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlmodel import Session

from app.ai.base import BaseAgent, ToolResult
from app.core import memory
from app.domain.money import cents_to_str
from app.models import Conversation, Group, Member, utcnow


@dataclass
class Chat:
    session: Session
    member: Member
    now: datetime | None = None                 # tests pin the clock; None = the real time
    conversation: Conversation | None = None    # where the steps are logged

    @property
    def group(self) -> Group:
        return self.session.get(Group, self.member.group_id)

    @property
    def time(self) -> datetime:
        return self.now or utcnow()


class ChatAgent(BaseAgent):
    context = Chat
    context_tag = "chat_context"

    def instructions(self, chat: Chat) -> str:
        """The facts of this run, at the end of the prompt in <chat_context> (docs/PROMPTS.md §2): who's chatting,
        their own requests and payouts, and the item list with who's getting what. Built again for every model call,
        so it's always current."""
        return (f"Group: {chat.group.name}. Member: {chat.member.name} ({chat.member.role}). Now: {when(chat.time)}.\n\n"
                f"{member_profile(chat)}\n\n"
                f"{item_list(memory.item_board(chat.session, chat.group, chat.time), chat.time)}")

    def middleware(self) -> list[AgentMiddleware]:
        return [_LogSteps(self)]


class _LogSteps(AgentMiddleware):
    """Every model call (how long it took, the tokens) and every tool call and its result (refused and failed ones
    too, with how long they took) go to the agent log: why the bot said what it said, and where the time went."""

    def __init__(self, agent: BaseAgent):
        super().__init__()
        self.agent = agent

    def _log(self, chat: Chat, event: str, name: str, data) -> None:
        memory.log_step(chat.session, agent=self.agent.name, event=event, conversation=chat.conversation, name=name,
                        data=data, prompt_id=self.agent.prompt_id)

    def _model(self, request: ModelRequest, response: ModelResponse, started: float) -> None:
        reply = next((m for m in response.result if isinstance(m, AIMessage)), None)
        usage = (reply.usage_metadata if reply else None) or {}
        self._log(request.runtime.context, "model", getattr(request.model, "model", "") or "",
                  {"ms": _ms(started), "input_tokens": usage.get("input_tokens"),
                   "output_tokens": usage.get("output_tokens"), "tool_calls": len(reply.tool_calls) if reply else 0})
        for rejected in (m for m in response.result if isinstance(m, ToolMessage) and m.status == "error"):
            self._log(request.runtime.context, "rejected", rejected.name or "", rejected.content)  # by _CheckResult

    def _result(self, request: ToolCallRequest, result, started: float) -> None:
        if isinstance(result, ToolMessage):
            self._log(request.runtime.context, "tool_result", request.tool_call["name"],
                      {"status": result.status, "content": result.content, "ms": _ms(started)})

    def wrap_model_call(self, request, handler):
        started = time.monotonic()
        response = handler(request)
        self._model(request, response, started)
        return response

    async def awrap_model_call(self, request, handler):
        started = time.monotonic()
        response = await handler(request)
        self._model(request, response, started)
        return response

    def wrap_tool_call(self, request, handler):
        self._log(request.runtime.context, "tool_call", request.tool_call["name"], request.tool_call["args"])
        started = time.monotonic()
        result = handler(request)
        self._result(request, result, started)
        return result

    async def awrap_tool_call(self, request, handler):
        self._log(request.runtime.context, "tool_call", request.tool_call["name"], request.tool_call["args"])
        started = time.monotonic()
        result = await handler(request)
        self._result(request, result, started)
        return result


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def when(moment: datetime) -> str:
    return f"{moment:%a %d %b, %H:%M} UTC"


def member_said(chat: Chat) -> str:
    """Everything the member wrote in this conversation (lowercase, spaces tidied), to check quotes against."""
    if chat.conversation is None:
        return ""
    said = [m.text for m in memory.recent_messages(chat.session, chat.conversation) if m.role == "user"]
    return "\n".join(" ".join(text.lower().split()) for text in said)


def ago(then: datetime, now: datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 120:
        return f"{minutes} minutes ago"
    return f"{minutes // 60} hours ago" if minutes < 48 * 60 else f"{minutes // (24 * 60)} days ago"


def member_profile(chat: Chat) -> str:
    """The <member> block: the member's own requests, limit, receipts waiting and questions for them (their own
    data only, amounts included)."""
    m = chat.member
    status = memory.member_status(chat.session, m, chat.time)
    lines = ["<member>",
             f"{m.name} ({m.role}). PayPal email for payouts: "
             f"{'set' if m.paypal_email else 'not set yet (needed before their first payout)'}.",
             f"This month: {cents_to_str(status.committed_cents)} of {m.name}'s "
             f"{cents_to_str(status.monthly_limit_cents)} limit used or promised."]
    lines.append(f"{m.name}'s open requests:" if status.open_requests else f"{m.name} has no open requests.")
    for r in status.open_requests:
        lines.append(f"- {r.item}: on it until {when(r.until)} (request {r.request_id}, confirmed "
                     f"{ago(r.created_at, chat.time)})" if r.status == "on_it" else
                     f"- {r.item}: waiting for a PIC (request {r.request_id}, asked {ago(r.created_at, chat.time)})")
    if status.claims_waiting:
        lines.append(f"{m.name}'s receipts waiting for payout:")
    for c in status.claims_waiting:
        due = (f"paid on {c.payout_due_at:%a %d %b}" if c.payout_due_at else
               "a PIC is checking it first" if c.status == "review" else "payout date not set yet")
        held = " (on hold: someone reported a problem)" if c.status == "held" else ""
        lines.append(f"- {c.item}, {cents_to_str(c.total_cents)}: {due}{held} (claim {c.claim_id})")
    questions = memory.questions_for(chat.session, m)
    if questions:
        lines.append(f"Questions others asked about {m.name}'s purchases:")
    for q in questions:
        lines.append(f"- {q.asked_by}, about {q.item}: \"{q.text}\" (question {q.question_id})")
    return "\n".join(lines + ["</member>"])


PROOF = {"receipt": "receipt", "receipt_photo": "receipt + photo of where it's kept"}


SECTIONS = [
    ("being_got", "Someone is getting these right now: don't let anyone double up."),
    ("running_low", "Nobody's getting these and they've probably run out: suggest them."),
    ("recently_bought", "Bought lately, so probably still stocked: getting one again this soon needs a PIC."),
    ("available", "Nobody's getting these and none was bought lately: anyone can get one."),
]


def item_list(board: memory.ItemBoard, now: datetime) -> str:
    """The <items> block: every common item, grouped by where it stands (memory.ItemBoard), one line each plus a line
    on who's getting it or when it was last bought. Empty groups are left out."""
    def last_bought(i: memory.BoardItem) -> str:
        if not i.last_bought:
            return "never bought"
        days = (now - i.last_bought).days
        return f"last bought {'today' if days == 0 else f'{days} days ago'} by {i.last_bought_by}"

    def getting(o: memory.OnIt) -> str:
        return f"{o.member}, until {when(o.until)} (request {o.request_id})"

    if not board.items and not board.others_on_it:
        return "<items>\nThe group has no common items yet: everything needs a PIC's approval.\n</items>"
    sections = []
    for status, meaning in SECTIONS:
        lines = []
        for i in board.with_status(status):
            also = f"; also called {', '.join(i.other_names)}" if i.other_names else ""
            lines.append(f"- {i.name} (item_id {i.item_id}{also}) | {i.category} | up to {cents_to_str(i.cap_cents)} | "
                         f"lasts about {i.restock_days} days | proof: {PROOF.get(i.proof, i.proof)}")
            lines.append("  " + ("; ".join(getting(o) for o in i.on_it) if i.on_it else last_bought(i)))
        if status == "being_got":
            for o in board.others_on_it:
                lines += [f"- {o.item} (not on the list; a PIC approved it)", "  " + getting(o)]
        if lines:
            sections.append("\n".join([f"<{status}>", meaning, *lines, f"</{status}>"]))
    return "<items>\n" + "\n\n".join(sections) + "\n</items>"


# --- typed results: what tools return and agents finish with, so code can check one against the other ---------------

class Record(BaseModel):
    """A fact a tool returned, as JSON the model reads and copies: money in dollars ("$25.00"), times in words
    ("Sat 17 Oct, 11:00 UTC"). `type` says which kind; no other fields are allowed, so a record can't pass as
    another kind."""
    model_config = ConfigDict(extra="forbid")


def returned(results: list[ToolResult], kinds: TypeAdapter) -> list[Record]:
    """The records this run's tools returned (refusals and errors aren't records)."""
    found = []
    for result in results:
        if result.ok:
            try:
                found.append(kinds.validate_json(result.content))
            except ValidationError:
                pass
    return found


def not_returned(field: str, records: list[Record], seen: list[Record]) -> list[str]:
    """A problem for each record in `field` that isn't exactly one a tool returned, with the fields that differ from
    the closest one."""
    problems = []
    for i, record in enumerate(records):
        if record in seen:
            continue
        alike = [r for r in seen if r.type == record.type]
        if not alike:
            problems.append(f"{field}[{i}]: no tool returned a {record.type} in this run")
            continue
        closest = min(alike, key=lambda r: len(_differences(record, r)))
        problems.append(f"{field}[{i}]: " + "; ".join(
            f"{key} is {json.dumps(wrote, ensure_ascii=False)} but the tool returned "
            f"{json.dumps(was, ensure_ascii=False)}" for key, wrote, was in _differences(record, closest)))
    return problems


def _differences(a: Record, b: Record) -> list[tuple[str, object, object]]:
    one, other = a.model_dump(mode="json"), b.model_dump(mode="json")
    return [(key, one.get(key), other.get(key)) for key in sorted(one.keys() | other.keys()) if one.get(key) != other.get(key)]
