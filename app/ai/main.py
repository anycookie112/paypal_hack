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
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlmodel import Session

from app.ai.agents import ActionAgent, LookupAgent
from app.ai.agents.context import Chat, ChatAgent
from app.ai.base import StreamEvent
from app.ai.model import AgentError
from app.core import memory, states
from app.models import Conversation, Member

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
        """<chat_context>, plus what moved since the last reply: the conversation has no times, so without it an
        earlier reply that's out of date looks like a mistake (a live run told Marco "I was wrong" about gloves Priya
        had bought since)."""
        return "\n\n".join(part for part in (super().instructions(chat), since_last_reply(chat)) if part)


def since_last_reply(chat: Chat) -> str:
    """The <since_your_last_reply> block: every request and claim that moved in the group since the AI last
    replied in this conversation (app/core/states.py). Empty before its first reply."""
    if chat.conversation is None:
        return ""
    replies = [m for m in memory.recent_messages(chat.session, chat.conversation) if m.role == "assistant"]
    if not replies:
        return ""
    last = replies[-1].created_at
    moved = states.moves_since(chat.session, chat.member, last)
    lines = [f"What moved in the group since your last reply ({last:%H:%M} UTC), oldest first:",
             *(f"- {m}" for m in moved)] if moved else [f"Nothing moved since your last reply ({last:%H:%M} UTC)."]
    return "\n".join(["<since_your_last_reply>", *lines, "</since_your_last_reply>"])


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
    agent = agent or coordinator()
    conversation = memory.get_conversation(session, member, channel=channel, external_id=external_id)
    history = [HumanMessage(m.text) if m.role == "user" else AIMessage(m.text)
               for m in memory.recent_messages(session, conversation)]
    memory.add_message(session, conversation, "user", text)
    return agent, Chat(session, member, now=now, conversation=conversation), [*history, HumanMessage(text)]


def _finish(agent: Coordinator, chat: Chat, answer: str) -> Reply:
    conversation: Conversation = chat.conversation
    memory.add_message(chat.session, conversation, "assistant", answer)
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
    memory.add_message(chat.session, conversation, "assistant", text)
    return Reply(text, conversation.id, failed=True)
