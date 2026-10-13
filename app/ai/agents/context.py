"""What every agent run gets: who's chatting. Tools act as this member and read through this session.

`ChatAgent` is the base of the group fund's agents: it runs with a `Chat` and logs every tool call and result to the
agent log (docs/SPEC.md §14.2), so "why did the bot say that?" can always be answered.
"""

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from sqlmodel import Session

from app.ai.base import BaseAgent
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
        """The facts of this run, at the end of the prompt in <chat_context> (docs/PROMPTS.md §2): who's chatting, and
        the item list with who's getting what. Built again for every model call, so it's always current."""
        return (f"Group: {chat.group.name}. Member: {chat.member.name} ({chat.member.role}). Now: {when(chat.time)}.\n\n"
                f"{item_list(memory.item_board(chat.session, chat.group, chat.time), chat.time)}")

    def middleware(self) -> list[AgentMiddleware]:
        return [_LogSteps(self)]


class _LogSteps(AgentMiddleware):
    """Every tool call and its result (refused and failed ones too) go to the agent log."""

    def __init__(self, agent: BaseAgent):
        super().__init__()
        self.agent = agent

    def _log(self, request: ToolCallRequest, event: str, data) -> None:
        chat: Chat = request.runtime.context
        memory.log_step(chat.session, agent=self.agent.name, event=event, conversation=chat.conversation,
                        name=request.tool_call["name"], data=data, prompt_id=self.agent.prompt_id)

    def _result(self, request: ToolCallRequest, result) -> None:
        if isinstance(result, ToolMessage):
            self._log(request, "tool_result", {"status": result.status, "content": result.content})

    def wrap_tool_call(self, request, handler):
        self._log(request, "tool_call", request.tool_call["args"])
        result = handler(request)
        self._result(request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        self._log(request, "tool_call", request.tool_call["args"])
        result = await handler(request)
        self._result(request, result)
        return result


def when(moment: datetime) -> str:
    return f"{moment:%a %d %b, %H:%M} UTC"


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


def facts(value) -> str:
    """A tool's result as JSON the model reads easily: money in dollars ("cap": "$25.00"), times in words."""
    def tidy(v):
        if is_dataclass(v):
            names = [f.name for f in fields(v)] + [n for n, a in vars(type(v)).items() if isinstance(a, property)]
            v = {n: getattr(v, n) for n in names}
        if isinstance(v, dict):
            return {k.removesuffix("_cents"): cents_to_str(x) if k.endswith("_cents") and isinstance(x, int)
                    else tidy(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [tidy(x) for x in v]
        if isinstance(v, datetime):
            return when(v)
        return v
    return json.dumps(tidy(value), ensure_ascii=False)
