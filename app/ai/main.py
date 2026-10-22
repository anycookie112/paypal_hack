"""Where the AI comes together: the coordinator, joined with its subagents, and `reply` for one chat message.

    member ─▶ Coordinator ──lookup──▶ LookupAgent (read tools)  ──▶ LookupResult ─┐
                   ▲      └──act────▶ ActionAgent (action tools) ──▶ ActResult ────┤
                   └───────────────────────────────────────────────────────────────┘ ─▶ the reply

    answer = await reply(session, member, "We're out of gloves, I'll grab some by 1")

`reply` keeps the conversation: it loads the last messages, saves the new message and the answer, and every tool call
on the way is in the agent log. The web API and (later) the chat bots call it the same way.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlmodel import Session

from app.ai.agents import ActionAgent, LookupAgent
from app.ai.agents.context import Chat, ChatAgent
from app.ai.base import StreamEvent
from app.ai.model import AgentError
from app.core import memory, states
from app.models import Conversation, Member, utcnow

SORRY = "Sorry, I couldn't finish that just now. Please try again in a moment."
SORRY_PARTLY = ("Sorry, I couldn't finish that, but some of it may have gone through. Check your requests before "
                "trying again.")


class Coordinator(ChatAgent):
    """Talks to the member and asks its two subagents. It answers in text; the only agent the member hears from."""
    name = "coordinator"
    prompt = "coordinator"
    max_model_calls = 10
    max_tool_calls = 8

    def __init__(self, *, lookup: LookupAgent | None = None, action: ActionAgent | None = None, **kwargs):
        """`lookup` and `action` replace the default subagents (tests pass ones with fake models). Other arguments
        are BaseAgent's: the model, a prompt version, limits."""
        self.lookup = lookup or LookupAgent()
        self.action = action or ActionAgent()
        super().__init__(**kwargs)

    def setup(self):
        self.register_tool(self.lookup.as_tool("lookup"))
        self.register_tool(self.action.as_tool("act"))

    def sections(self) -> list[str]:
        return [states.lifecycle()]

    def instructions(self, chat: Chat) -> str:
        """<chat_context>, plus what each person did recently (<people>). The conversation alone doesn't show what
        happened in the group between messages: a live run told Marco "I was wrong" about gloves Priya bought after
        it spoke."""
        return "\n\n".join(part for part in (super().instructions(chat), people(chat)) if part)


def people(chat: Chat, hours: int = 24) -> str:
    """The <people> block: what each person in the group did in the last `hours`, oldest first, from the records
    (app/core/memory.py `recent_activity`), and when the AI last replied, so news since then is plain."""
    done = memory.recent_activity(chat.session, chat.member, chat.time - timedelta(hours=hours))
    lines = ["<people>", f"What each person in the group did in the last {hours} hours, oldest first, from the "
             "group's records. Times are UTC."]
    replies = [m for m in memory.recent_messages(chat.session, chat.conversation)
               if m.role == "assistant"] if chat.conversation else []
    if replies:
        lines.append(f"Your last reply in this chat was at {stamp(replies[-1].created_at, chat.time)}: anything after "
                     f"it is news to {chat.member.name}. Each of your replies was true when you sent it.")
    by_person: dict[str, list[memory.Done]] = {}
    for d in done:
        by_person.setdefault(d.member, []).append(d)
    for name, theirs in by_person.items():
        you = " (the member you're talking to)" if name == chat.member.name else ""
        lines.append(f"- {name}{you}: " + " · ".join(f"{stamp(d.at, chat.time)} {d.text}" for d in theirs))
    if not done:
        lines.append("Nobody did anything.")
    return "\n".join(lines + ["</people>"])


def stamp(at: datetime, now: datetime) -> str:
    """A time for the AI: "07:45" today, "Fri 07:45" on another day."""
    return f"{at:%H:%M}" if at.date() == now.date() else f"{at:%a %H:%M}"


@dataclass
class Reply:
    text: str
    conversation_id: int
    failed: bool = False  # the AI couldn't answer; `text` is an apology


_coordinator: Coordinator | None = None


def coordinator() -> Coordinator:
    """The app's coordinator, built once (its graph is reused for every message)."""
    global _coordinator
    if _coordinator is None:
        _coordinator = Coordinator()
    return _coordinator


async def reply(session: Session, member: Member, text: str, *, agent: Coordinator | None = None,
                channel: str = "web", external_id: str = "", now: datetime | None = None) -> Reply:
    """Answer one message from `member`, in their conversation on `channel`."""
    agent, chat, messages = _start(session, member, text, agent, channel, external_id, now)
    try:
        answer = await agent.run(messages, context=chat)
    except AgentError as exc:
        return _failed(agent, chat, exc)
    return _finish(agent, chat, answer)


async def stream_reply(session: Session, member: Member, text: str, *, agent: Coordinator | None = None,
                       channel: str = "web", external_id: str = "", now: datetime | None = None
                       ) -> AsyncIterator[StreamEvent]:
    """`reply`, streamed: the answer as it's written and the coordinator's tool calls, then an "output" event whose
    data is the `Reply`."""
    agent, chat, messages = _start(session, member, text, agent, channel, external_id, now)
    try:
        async for event in agent.stream(messages, context=chat):
            if event.type == "output":
                yield StreamEvent("output", agent.name, data=_finish(agent, chat, event.data))
            else:
                yield event
    except AgentError as exc:
        yield StreamEvent("output", agent.name, data=_failed(agent, chat, exc))


def _start(session, member, text, agent, channel, external_id, now) -> tuple[Coordinator, Chat, list[BaseMessage]]:
    """The member's messages start with when they sent them ("[07:22] …"), so the AI can place its earlier replies
    against what happened in the group since (<people>)."""
    agent = agent or coordinator()
    conversation = memory.get_conversation(session, member, channel=channel, external_id=external_id)
    said = utcnow() if now is None else now
    history = [HumanMessage(f"[{stamp(m.created_at, said)}] {m.text}") if m.role == "user" else AIMessage(m.text)
               for m in memory.recent_messages(session, conversation)]
    memory.add_message(session, conversation, "user", text, now=now)
    return agent, Chat(session, member, now=now, conversation=conversation), [
        *history, HumanMessage(f"[{stamp(said, said)}] {text}")]


def _finish(agent: Coordinator, chat: Chat, answer: str) -> Reply:
    conversation: Conversation = chat.conversation
    memory.add_message(chat.session, conversation, "assistant", answer, now=chat.now)
    memory.log_step(chat.session, agent=agent.name, event="output", conversation=conversation, data=answer,
                    prompt_id=agent.prompt_id)
    return Reply(answer, conversation.id)


def _acted(completed: list[dict]) -> bool:
    """Whether the action agent changed anything before the coordinator failed (lookups change nothing)."""
    for step in completed:
        if step["tool"] != "act":
            continue
        try:
            result = json.loads(step["result"])
        except (TypeError, ValueError):
            return True  # can't tell: assume it did
        if result.get("status") == "done" or result.get("completed"):
            return True
    return False


def _failed(agent: Coordinator, chat: Chat, exc: AgentError) -> Reply:
    conversation: Conversation = chat.conversation
    memory.log_step(chat.session, agent=agent.name, event="error", conversation=conversation,
                    data={"reason": str(exc), "completed": exc.completed}, prompt_id=agent.prompt_id)
    text = SORRY_PARTLY if _acted(exc.completed) else SORRY
    memory.add_message(chat.session, conversation, "assistant", text, now=chat.now)
    return Reply(text, conversation.id, failed=True)
