"""The base class every agent inherits from, on LangChain's `create_agent` (a LangGraph graph: model ⇄ tools).

    class LookupAgent(BaseAgent):
        name = "lookup"
        prompt = "lookup"            # a versioned prompt in prompts.py
        context = Chat               # who's chatting: what every tool and `instructions` get
        output = LookupResult        # optional: the agent must finish by filling this in

        def setup(self):
            self.register_tool(self.who_is_on_it)

        def instructions(self, chat: Chat) -> str:   # optional: added to the prompt on every model call
            return f"You're talking to {chat.member.name}."

        def who_is_on_it(self, runtime: ToolRuntime[Chat]) -> list[OnIt]:
            '''Everyone getting something right now.'''
            return memory.who_is_on_it(runtime.context.session, runtime.context.group)

    lookup = LookupAgent(max_model_calls=6, timeout_seconds=120)   # limits can be set per agent
    answer = await lookup.run("is anyone getting gloves?", context=chat)       # normal output
    async for event in lookup.stream("is anyone getting gloves?", context=chat):  # streamed output
        ...
    coordinator.register_tool(lookup.as_tool())   # a subagent: its result is the coordinator's tool result

**Finishing.** An agent with an `output` model must finish by calling it (its name is the model's class name, e.g.
`LookupResult`): a plain-text answer is sent back with a reminder until it does. Its arguments are validated;
mistakes go back to the model to fix. This is Gemini CLI's `complete_task` and Goose's
`final_output`.

**Errors are tool results, not crashes.** A tool that raises `ActionError` (a rule said no: "Marco is already getting
them") sends that message back to the model. Any other exception is logged and the model is told the tool failed.
The same tool called with the same arguments too often is refused. A subagent that fails (out of calls or time, or
the provider is down, a bug) comes back to the coordinator as `{"status": "failed", "reason", "completed"}`, where
`completed` lists what its tools had already done: an action can have happened even though the agent never finished.
Called directly, `run` and `stream` raise `AgentError` with the same `completed` list.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal

import openai
from langchain.agents import create_agent
from langchain.agents.middleware import (AgentMiddleware, ModelCallLimitMiddleware, ModelRequest,
                                         ToolCallLimitMiddleware, ToolCallRequest, ToolErrorMiddleware,
                                         dynamic_prompt, hook_config)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from app.ai import prompts
from app.ai.model import AgentError, default_model
from app.core.requests import ActionError

log = logging.getLogger(__name__)

_FAILURES = (ModelCallLimitExceededError, ToolCallLimitExceededError, GraphRecursionError, openai.APIError,
             TimeoutError)


@dataclass
class StreamEvent:
    """One step of a streamed run, in a shape the API can send to the browser as-is."""
    type: Literal["text", "tool_call", "tool_result", "output"]
    agent: str
    text: str = ""   # a piece of the reply as it's written
    name: str = ""   # the tool
    data: Any = None # the tool's arguments or result; the final output

    def to_json(self) -> dict:
        return {"type": self.type, "agent": self.agent, "text": self.text, "name": self.name,
                "data": to_jsonable_python(self.data)}


class BaseAgent:
    name: ClassVar[str] = "agent"
    description: ClassVar[str] = ""          # what it does: the tool description when it's a subagent
    prompt: ClassVar[str | None] = None      # name of a versioned prompt in prompts.py
    context: ClassVar[type] = type(None)     # what every run gets: who's chatting, the database session
    output: ClassVar[type[BaseModel] | None] = None  # the result it must finish with; None = a text answer
    model_name: ClassVar[str | None] = None  # another model on the same provider (e.g. one that reads images)

    # Limits per run. Defaults for every agent of this class; each can be changed when it's created.
    max_model_calls: ClassVar[int] = 8
    max_tool_calls: ClassVar[int] = 10
    max_repeats: ClassVar[int] = 2                # the same tool with the same arguments; more is refused
    timeout_seconds: ClassVar[float | None] = None  # the whole run; None = only the provider's per-call timeout

    def __init__(self, *, model: BaseChatModel | None = None, prompt_version: int | None = None,
                 max_model_calls: int | None = None, max_tool_calls: int | None = None,
                 max_repeats: int | None = None, timeout_seconds: float | None = None):
        """`model` replaces the provider's model (tests pass a fake one); `prompt_version` pins a prompt version.
        The limits override the class's defaults for this agent."""
        self._model = model
        versioned = prompts.get(self.prompt, prompt_version) if self.prompt else None
        self.prompt_text = versioned.text if versioned else ""
        self.prompt_id = versioned.id if versioned else ""  # e.g. "lookup@v2", for the agent log
        for limit, value in (("max_model_calls", max_model_calls), ("max_tool_calls", max_tool_calls),
                             ("max_repeats", max_repeats), ("timeout_seconds", timeout_seconds)):
            if value is not None:
                setattr(self, limit, value)
        self.tools: list[BaseTool] = []
        self._graph = None
        self.setup()

    def setup(self) -> None:
        """Override to register tools."""

    def instructions(self, context: Any) -> str:
        """Override to add instructions that depend on the run, e.g. who's chatting and today's date."""
        return ""

    def register_tool(self, func: Callable | BaseTool) -> None:
        """Let the model call `func`: a function or method, or a LangChain tool. The docstring says what it does;
        its Google-style `Args:` section describes each argument."""
        if self._graph is not None:
            raise RuntimeError(f"{self.name} is already initialized; register tools in setup()")
        self.tools.append(func if isinstance(func, BaseTool)
                          else tool(func, parse_docstring=True, error_on_invalid_docstring=False))

    def initialize(self):
        """Build the graph. Runs on first use."""
        if self._graph is None:
            @dynamic_prompt
            def system_prompt(request: ModelRequest) -> str:
                return "\n\n".join(p for p in (self.prompt_text, self.instructions(request.runtime.context)) if p)

            self._graph = create_agent(
                self._model or default_model(self.model_name), self.tools, response_format=self.output,
                context_schema=self.context, name=self.name,
                # _MustFinish comes first: its "back to the model" runs after the call limit has counted the call.
                middleware=[*([_MustFinish(self.output)] if self.output else []),
                            system_prompt,
                            ModelCallLimitMiddleware(run_limit=self.max_model_calls, exit_behavior="error"),
                            ToolCallLimitMiddleware(run_limit=self.max_tool_calls, exit_behavior="error"),
                            ToolErrorMiddleware(on_error=_back_to_the_model),
                            _RefuseRepeats(self.max_repeats)])
        return self._graph

    async def run(self, messages: str | Sequence[BaseMessage], *, context: Any = None) -> Any:
        """Run to the end and return the answer: text, or the `output` model."""
        state = None  # the latest state, kept step by step so a failure can say what was already done
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for state in self.initialize().astream(_input(messages), context=context, config=self._config,
                                                             stream_mode="values"):
                    pass
        except Exception as exc:
            raise self._failed(exc, state) from exc
        return _answer(state)

    async def stream(self, messages: str | Sequence[BaseMessage], *, context: Any = None
                     ) -> AsyncIterator[StreamEvent]:
        """Run and yield what happens: the reply as it's written, tool calls and results, then one "output" event."""
        state = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for mode, chunk in self.initialize().astream(_input(messages), context=context,
                                                                   config=self._config,
                                                                   stream_mode=["messages", "updates", "values"]):
                    if mode == "values":
                        state = chunk
                    elif mode == "messages":
                        message, meta = chunk
                        if (isinstance(message, AIMessageChunk) and message.text
                                and meta.get("langgraph_node") == "model"):
                            yield StreamEvent("text", self.name, text=message.text)
                    else:
                        for event in _tool_events(self.name, chunk):
                            yield event
        except Exception as exc:
            raise self._failed(exc, state) from exc
        yield StreamEvent("output", self.name, data=_answer(state))

    def _failed(self, exc: Exception, state: dict | None) -> AgentError:
        """Why the run failed, with what its tools had already done. Anything unexpected is logged as a bug."""
        if isinstance(exc, AgentError):  # a subagent's own failure, already explained
            reason = str(exc)
        elif isinstance(exc, _FAILURES):
            reason = f"{self.name}: {_why(exc, self)}"
        else:
            log.exception("%s failed unexpectedly", self.name, exc_info=exc)
            reason = f"{self.name}: failed unexpectedly ({type(exc).__name__})"
        return AgentError(reason, completed=_completed(state, self.output))

    @property
    def _config(self) -> dict:
        # A backstop under the call limits (which give the clearer error): a few graph steps per model call.
        return {"recursion_limit": 10 * (self.max_model_calls + 2)}

    def as_tool(self, name: str | None = None, description: str | None = None) -> BaseTool:
        """This agent as a subagent another agent can ask. It runs with the same context but none of the asking
        agent's conversation, so the request must say everything. Its result (or why it failed) is the tool's
        result; it never raises."""
        about = description or self.description or f"Ask the {self.name} agent."

        @tool(name or self.name, description=f"{about} It sees nothing of this conversation: include every id and "
                                             "detail it needs in the request.")
        async def ask(request: Annotated[str, "What you need, with every id and detail it needs."],
                      runtime: ToolRuntime[Any]) -> str:
            try:
                result = await self.run(request, context=runtime.context)
            except AgentError as exc:
                return json.dumps({"status": "failed", "reason": str(exc), "completed": exc.completed},
                                  ensure_ascii=False)
            return result if isinstance(result, str) else json.dumps(to_jsonable_python(result), ensure_ascii=False)
        return ask

    def __repr__(self) -> str:
        prompt = f" ({self.prompt_id})" if self.prompt_id else ""
        return f"<{type(self).__name__} {self.name!r}{prompt}: {len(self.tools)} tools>"


class _MustFinish(AgentMiddleware):
    """An agent with an `output` model finishes only by calling it: a plain-text answer is sent back to the model
    with a reminder (the model call limit stops a model that never does)."""

    def __init__(self, output: type[BaseModel]):
        super().__init__()
        self.output = output.__name__

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict | None:
        reply = state["messages"][-1]
        if isinstance(reply, AIMessage) and not reply.tool_calls:
            return {"messages": [HumanMessage(f"Finish by calling {self.output}: only that is read.")],
                    "jump_to": "model"}
        return None


class _RefuseRepeats(AgentMiddleware):
    """The same tool with the same arguments, called again and again, won't give a different answer: after
    `limit` times it's refused, so the model uses what it has (Gemini CLI, Cline and OpenHands all catch this)."""

    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        earlier = sum(1 for m in request.state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls
                      if c["id"] != call["id"] and c["name"] == call["name"] and c["args"] == call["args"])
        if earlier < self.limit:
            return None
        return ToolMessage(f"Not run: {call['name']} was already called {earlier} times with these arguments. "
                           "Use the earlier result, or finish.", tool_call_id=call["id"], name=call["name"],
                           status="error")

    def wrap_tool_call(self, request, handler):
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._refusal(request) or await handler(request)


def _back_to_the_model(error: Exception, request: ToolCallRequest) -> str:
    if isinstance(error, ActionError):
        return str(error)
    log.exception("Tool %s failed", request.tool_call["name"], exc_info=error)
    return (f"{request.tool_call['name']} failed unexpectedly ({type(error).__name__}). Don't retry it: say "
            "something went wrong.")


def _why(exc: Exception, agent: BaseAgent) -> str:
    if isinstance(exc, TimeoutError):
        return f"took longer than {agent.timeout_seconds:g} s" if agent.timeout_seconds else "the model timed out"
    return str(exc)


def _tool_events(agent: str, update: dict) -> list[StreamEvent]:
    events = []
    for node in update.values():
        for message in (node or {}).get("messages", []) if isinstance(node, dict) else []:
            if isinstance(message, AIMessage):
                events += [StreamEvent("tool_call", agent, name=c["name"], data=c["args"]) for c in message.tool_calls]
            elif isinstance(message, ToolMessage):
                events.append(StreamEvent("tool_result", agent, name=message.name or "", data=message.content))
    return events


def _input(messages: str | Sequence[BaseMessage]) -> dict:
    return {"messages": [HumanMessage(messages)] if isinstance(messages, str) else list(messages)}


def _completed(state: dict | None, output: type[BaseModel] | None) -> list[dict]:
    """The tool calls that worked in this run, oldest first (refusals and errors didn't do anything)."""
    messages = (state or {}).get("messages", [])
    return [{"tool": m.name, "result": m.content} for m in messages
            if isinstance(m, ToolMessage) and m.status != "error" and not (output and m.name == output.__name__)]


def _answer(state: dict) -> Any:
    return state.get("structured_response") or state["messages"][-1].text
