"""A fake LangChain chat model for tests: a function decides each reply, so no network is needed."""

import json
import re
from collections.abc import Callable
from itertools import count

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

_ids = count(1)


def call(name: str, args: dict | None = None, text: str = "") -> AIMessage:
    """A reply that calls a tool (optionally after some text)."""
    return AIMessage(text, tool_calls=[{"name": name, "args": args or {}, "id": f"call_{next(_ids)}"}])


def tool_names(tools: list[dict]) -> list[str]:
    return [t["function"]["name"] for t in tools]


class FakeChatModel(BaseChatModel):
    """`respond(messages, tools)` returns the reply: a string, or an AIMessage (see `call`).
    `tools` are the OpenAI tool specs the agent bound. Every call's settings are kept in `calls`."""
    respond: Callable[[list[BaseMessage], list[dict]], str | AIMessage]
    calls: list[dict] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _reply(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        self.calls.append(kwargs)
        reply = self.respond(messages, kwargs.get("tools", []))
        return AIMessage(reply) if isinstance(reply, str) else reply

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages, kwargs))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._reply(messages, kwargs)
        for word in re.findall(r"\S+\s*", reply.text):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word))
        for i, tc in enumerate(reply.tool_calls):
            chunk = tool_call_chunk(name=tc["name"], args=json.dumps(tc["args"]), id=tc["id"], index=i)
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk]))
