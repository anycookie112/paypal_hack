"""Base class for the app's AI agents, on top of LangChain (models, messages) and LangGraph (the run loop).

An agent is a subclass that declares what it is, and registers prompts and tools:

    class OrderAgent(BaseAgent[ShopDeps, OrderProposal]):
        name = "order_builder"
        description = "Turns a customer's request into an order from the shop's products."
        deps_type = ShopDeps
        output_type = OrderProposal
        prompt = "order_builder"   # versioned instructions from prompts.py

        @agent_prompt
        def shop_context(self, ctx: RunContext[ShopDeps]) -> str:
            return f"The shop is {ctx.deps.business.name}."

        @agent_tool
        async def find_products(self, ctx: RunContext[ShopDeps], query: str) -> list[ProductView]:
            '''Search the shop's products by name, category or tag.'''

        @agent_validator
        def check(self, ctx: RunContext[ShopDeps], output: OrderProposal) -> OrderProposal:
            ...  # raise ModelRetry("why it's wrong") to make the model fix its answer

    agent = OrderAgent()
    result = await agent.run("Party for 20 kids, $120", deps=deps)        # normal output
    async for event in agent.stream("Party for 20 kids, $120", deps=deps):  # streamed output
        ...

Each agent compiles to a LangGraph graph that loops until the model gives a final answer:

    START -> model -> tools -> model -> tools -> ... -> END

`model` calls the LLM with the agent's instructions and tools. `tools` acts on its reply: runs the
tool calls, checks a final answer (its schema, then the validators), or ends the run on a handover.
Anything wrong goes back to the model to fix, up to `retries` times. The run's `RunContext` (deps and
usage) is the graph's runtime context, and it's what tools, dynamic prompts and validators get as `ctx`.

The graph is built on first use (`initialize()`), so prompts, tools and handovers can also be
registered at runtime with `register_prompt`, `register_tool` and `handover_to` until then.

A handover passes the conversation to another agent, which produces the final output in its place:
`triage.handover_to(order_agent)` lets the triage agent decide "this is an order" and hand over.
The model only makes the decision (by calling a handover tool with a brief); this class then runs
the target, so the target's events stream live.

Final answers that aren't plain text come back through an output tool ("final_result") whose
arguments are the output type's schema, so the model can use tools and answer in the same run.
"""

import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar, Generic, Literal, TypedDict, TypeVar, get_origin, get_type_hints

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, ValidationError, create_model
from pydantic_core import to_jsonable_python

from app.ai import prompts
from app.ai.model import AgentError, default_model

DepsT = TypeVar("DepsT")
OutputT = TypeVar("OutputT")

_TOOL_MARK = "__agent_tool__"
_PROMPT_MARK = "__agent_prompt__"
_VALIDATOR_MARK = "__agent_validator__"

OUTPUT_TOOL = "final_result"


class ModelRetry(Exception):
    """Raise in a tool or validator to send the message back to the model so it can fix its call or answer."""


@dataclass
class Usage:
    requests: int = 0      # model calls
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, reply: AIMessage) -> None:
        self.requests += 1
        if reply.usage_metadata:
            self.input_tokens += reply.usage_metadata.get("input_tokens", 0)
            self.output_tokens += reply.usage_metadata.get("output_tokens", 0)


@dataclass
class RunContext(Generic[DepsT]):
    """What tools, dynamic prompts and validators get as `ctx`: the run's deps, and its usage so far."""
    deps: DepsT
    usage: Usage = field(default_factory=Usage)


def agent_tool(func: Callable | None = None, *, name: str | None = None, retries: int | None = None):
    """Mark a method as a tool the model can call. The docstring is the tool's description; take
    `ctx: RunContext[Deps]` as the first parameter to read the run's dependencies."""
    def mark(f: Callable) -> Callable:
        setattr(f, _TOOL_MARK, {"name": name, "retries": retries})
        return f
    return mark(func) if func else mark


def agent_prompt(func: Callable) -> Callable:
    """Mark a method as instructions computed for every request, e.g. from `ctx.deps`."""
    setattr(func, _PROMPT_MARK, True)
    return func


def agent_validator(func: Callable) -> Callable:
    """Mark a method `(ctx, output) -> output` that checks the final output in code. Raising
    `ModelRetry("what's wrong")` sends the reason back to the model to fix (up to `retries` times)."""
    setattr(func, _VALIDATOR_MARK, True)
    return func


@dataclass
class AgentResult(Generic[OutputT]):
    output: OutputT
    handled_by: str  # the agent that produced the output; differs from the one called after a handover
    messages: list[BaseMessage]
    usage: Usage
    prompt_id: str = ""  # the prompt version that produced it, e.g. "order_builder@v1"


@dataclass
class StreamEvent:
    """One step of a streamed run, in a shape the API can send to the browser as-is."""
    type: Literal["text", "tool_call", "tool_result", "handover", "output"]
    agent: str
    text: str = ""          # text delta, or the brief passed on a handover
    name: str = ""          # tool name, or the agent handed over to
    data: Any = None        # tool arguments or tool result
    result: AgentResult | None = field(default=None, repr=False)  # on the final "output" event

    def to_json(self) -> dict:
        data = self.result.output if self.result is not None else self.data
        return {"type": self.type, "agent": self.agent, "text": self.text, "name": self.name,
                "data": to_jsonable_python(data)}


@dataclass
class _Handover:
    """How a run that handed over ends: `target` takes over with the model's brief."""
    target: "BaseAgent"
    brief: str

    def prompt(self, original: str) -> str:
        return f"{self.brief}\n\nThe user's original message:\n{original}"


# --- tools -----------------------------------------------------------------------------------------

def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """A Google-style docstring's description, and its `Args:` descriptions by name."""
    description, args = [], {}
    in_args, indent, current = False, None, None
    for line in inspect.cleandoc(doc or "").splitlines():
        if line.strip() in ("Args:", "Arguments:"):
            in_args = True
            continue
        if not in_args:
            description.append(line)
        elif not line.strip():
            continue
        elif not line.startswith(" "):
            in_args = False  # the next section, e.g. "Returns:"
        else:
            line_indent = len(line) - len(line.lstrip())
            match = re.match(r"(\w+)(?: \(.*?\))?:\s*(.*)", line.strip())
            if match and (indent is None or line_indent == indent):
                indent, current = line_indent, match.group(1)
                args[current] = match.group(2)
            elif current:
                args[current] += " " + line.strip()
    return "\n".join(description).strip(), args


def _takes_ctx(param: inspect.Parameter | None, hint: Any) -> bool:
    return param is not None and (param.name == "ctx" or hint is RunContext or get_origin(hint) is RunContext)


@dataclass
class _Tool:
    """A function the model can call: its JSON schema for the model, and how to call it."""
    name: str
    func: Callable
    schema: type[BaseModel]
    description: str
    takes_ctx: bool
    max_retries: int | None = None

    @classmethod
    def from_function(cls, func: Callable, *, name: str | None = None, description: str | None = None,
                      retries: int | None = None) -> "_Tool":
        name = name or func.__name__
        params = list(inspect.signature(func).parameters.values())
        hints = get_type_hints(func, include_extras=True)
        takes_ctx = _takes_ctx(params[0] if params else None, hints.get(params[0].name) if params else None)
        doc_description, arg_docs = _parse_docstring(func.__doc__)
        fields = {
            p.name: (hints.get(p.name, Any), Field(... if p.default is p.empty else p.default,
                                                    description=arg_docs.get(p.name)))
            for p in params[1 if takes_ctx else 0:]
        }
        return cls(name, func, create_model(name, **fields), description or doc_description, takes_ctx, retries)

    def spec(self) -> dict:
        tool = convert_to_openai_tool(self.schema)
        tool["function"].update(name=self.name, description=self.description)
        return tool

    async def call(self, ctx: RunContext, args: dict) -> Any:
        """Validate the model's arguments and call the function. Raises ValidationError or ModelRetry."""
        parsed = self.schema.model_validate(args)
        kwargs = {name: getattr(parsed, name) for name in type(parsed).model_fields}
        result = self.func(ctx, **kwargs) if self.takes_ctx else self.func(**kwargs)
        return await result if inspect.isawaitable(result) else result


def _as_text(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(to_jsonable_python(result), ensure_ascii=False)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_context=False)
        return f"{len(errors)} validation error(s): {json.dumps(errors, default=str)}"
    return str(exc)


async def _call_with_optional_ctx(func: Callable, ctx: RunContext, *args: Any) -> Any:
    takes_ctx = len(inspect.signature(func).parameters) > len(args)
    result = func(ctx, *args) if takes_ctx else func(*args)
    return await result if inspect.isawaitable(result) else result


# --- the agent -------------------------------------------------------------------------------------

class _State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    retries: dict[str, int]  # failed attempts so far, per tool and for the final output
    done: bool
    output: Any
    handover: _Handover | None


class BaseAgent(Generic[DepsT, OutputT]):
    name: ClassVar[str] = "agent"
    description: ClassVar[str] = ""
    deps_type: ClassVar[Any] = type(None)
    output_type: ClassVar[Any] = str
    prompt: ClassVar[str | None] = None  # name of a versioned prompt in prompts.py
    instructions: ClassVar[str] = ""     # unversioned instructions, for quick experiments and tests
    retries: ClassVar[int] = 2       # how often the model may retry a tool call or output that failed validation

    # Guards against runaway runs. A live test (2026-10-11) had Atria emit ~1,700 identical tool calls in
    # one never-ending reply; only the reply length cap stops that, since the other limits are checked
    # between replies.
    max_requests: ClassVar[int] = 12       # model calls per run, including handovers
    max_tool_calls: ClassVar[int] = 15     # tool calls per run
    max_reply_tokens: ClassVar[int] = 2000 # length of one model reply
    parallel_tool_calls: ClassVar[bool] = False  # one call per reply: the model sees each result before the next

    def __init__(self, *, model: BaseChatModel | None = None, prompt_version: int | None = None):
        """`model` overrides the configured LLM (tests pass a fake chat model).
        `prompt_version` pins a version of the agent's prompt instead of the latest."""
        self._model = model
        self._prompts: list[str | Callable] = []
        self.prompt_id = ""
        if self.prompt:
            versioned = prompts.get(self.prompt, prompt_version)
            self._prompts.append(versioned.text)
            self.prompt_id = versioned.id
        if self.instructions:
            self._prompts.append(self.instructions)
        self._tools: list[_Tool] = []
        self._handovers: dict[str, _Tool] = {}  # handover tool name -> tool returning a _Handover
        self._validators: list[Callable] = []
        self._graph = None
        self._register_marked_methods()
        self.setup()

    def setup(self) -> None:
        """Override to register prompts, tools or handovers in code."""

    # --- registration ----------------------------------------------------------------------

    def register_prompt(self, prompt: str | Callable) -> None:
        """Add instructions: a fixed string, or a function (optionally taking `ctx`) run for every request."""
        self._check_not_initialized()
        self._prompts.append(prompt)

    def register_tool(self, func: Callable, *, name: str | None = None, description: str | None = None,
                      retries: int | None = None) -> None:
        """Let the model call `func`. Parameters become the tool's schema; the docstring its description."""
        self._check_not_initialized()
        self._tools.append(_Tool.from_function(func, name=name, description=description, retries=retries))

    def register_validator(self, func: Callable) -> None:
        """Check the final output with `func(ctx, output) -> output`; raise ModelRetry to make the model fix it.
        Not called when the agent hands over (the target's own validators check its output)."""
        self._check_not_initialized()
        self._validators.append(func)

    def handover_to(self, target: "BaseAgent", *, name: str | None = None, description: str | None = None) -> None:
        """Let the model hand the conversation to `target`, whose output becomes this run's output.

        Both agents get the same deps and share the usage limit. The target receives the model's brief
        plus the user's original message; its stream events follow this agent's in the stream.
        """
        self._check_not_initialized()
        if target.deps_type is not self.deps_type and target.deps_type is not type(None):
            raise TypeError(f"{target.name} needs {target.deps_type.__name__} deps, "
                            f"but {self.name} has {getattr(self.deps_type, '__name__', self.deps_type)}")

        def handover(brief: str) -> _Handover:
            """
            Args:
                brief: What the next agent needs to know: the request and anything already established.
            """
            return _Handover(target, brief)

        tool = _Tool.from_function(handover, name=name or f"handover_to_{target.name}",
                                   description=description or f"Hand over to the {target.name} agent: {target.description}")
        self._handovers[tool.name] = tool

    def _register_marked_methods(self) -> None:
        marked: dict[str, Callable] = {}
        for klass in reversed(type(self).__mro__):
            for attr_name, attr in vars(klass).items():
                if any(hasattr(attr, mark) for mark in (_TOOL_MARK, _PROMPT_MARK, _VALIDATOR_MARK)):
                    marked[attr_name] = attr
        for attr_name, attr in marked.items():
            method = getattr(self, attr_name)
            if hasattr(attr, _PROMPT_MARK):
                self.register_prompt(method)
            elif hasattr(attr, _VALIDATOR_MARK):
                self.register_validator(method)
            else:
                opts = getattr(attr, _TOOL_MARK)
                self.register_tool(method, name=opts["name"] or attr_name, retries=opts["retries"])

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    def _check_not_initialized(self) -> None:
        if self._graph is not None:
            raise RuntimeError(f"{self.name} is already initialized; register prompts, tools and handovers first")

    # --- the graph -------------------------------------------------------------------------

    @property
    def _text_output(self) -> bool:
        return self.output_type is str

    def initialize(self):
        """Build the LangGraph graph from everything registered. Runs automatically on first use."""
        if self._graph is not None:
            return self._graph
        if self._text_output:
            self._output_tool = None
        else:
            # The output tool's arguments are the output type's fields, or {"response": ...} for other types.
            schema = (self.output_type if isinstance(self.output_type, type) and issubclass(self.output_type, BaseModel)
                      else create_model(OUTPUT_TOOL, response=(self.output_type, ...)))
            self._output_tool = _Tool(OUTPUT_TOOL, lambda **kwargs: None, schema,
                                      "The final response, which ends the conversation.", takes_ctx=False)
        callable_tools = [*self._tools, *self._handovers.values(), *([self._output_tool] if self._output_tool else [])]
        settings: dict[str, Any] = {"max_tokens": self.max_reply_tokens}
        if self._tools and not self.parallel_tool_calls:
            settings["parallel_tool_calls"] = False
        if not self._text_output:
            settings["tool_choice"] = "required"  # a text reply can't be the answer, so always call a tool
        llm = self._model or default_model()
        self._llm = llm.bind_tools([t.spec() for t in callable_tools], **settings) if callable_tools else llm.bind(**settings)

        graph = StateGraph(_State, context_schema=RunContext)
        graph.add_node("model", self._call_model)
        graph.add_node("tools", self._act)
        graph.add_edge(START, "model")
        graph.add_edge("model", "tools")
        graph.add_conditional_edges("tools", lambda s: END if s["done"] or s["handover"] else "model", ["model", END])
        self._graph = graph.compile(name=self.name)
        return self._graph

    async def _instructions(self, ctx: RunContext) -> str:
        parts = [p if isinstance(p, str) else await _call_with_optional_ctx(p, ctx) for p in self._prompts]
        return "\n\n".join(p for p in parts if p)

    async def _call_model(self, state: _State, runtime: Runtime[RunContext]) -> dict:
        ctx = runtime.context
        if ctx.usage.requests >= self.max_requests:
            raise AgentError(f"{self.name}: the limit of {self.max_requests} model requests was reached")
        instructions = await self._instructions(ctx)
        messages = [SystemMessage(instructions), *state["messages"]] if instructions else state["messages"]
        reply = await self._llm.ainvoke(messages)
        ctx.usage.add(reply)
        return {"messages": [reply]}

    async def _act(self, state: _State, runtime: Runtime[RunContext]) -> dict:
        """Act on the model's reply: run its tool calls, accept a final answer, or hand over."""
        ctx, write = runtime.context, runtime.stream_writer
        reply: AIMessage = state["messages"][-1]
        retries = dict(state["retries"])

        def retry(key: str, limit: int) -> None:
            retries[key] = retries.get(key, 0) + 1
            if retries[key] > limit:
                what = "the final output" if key == OUTPUT_TOOL else f"tool {key!r}"
                raise AgentError(f"{self.name}: exceeded the maximum of {limit} retries for {what}")

        if reply.invalid_tool_calls:  # arguments that weren't valid JSON
            for call in reply.invalid_tool_calls:
                retry(call.get("name") or "unknown", self.retries)
            return {"retries": retries, "messages": [
                ToolMessage(f"Invalid arguments: {call.get('error') or 'not valid JSON'}. Fix the errors and try again.",
                            tool_call_id=call.get("id") or "", status="error")
                for call in reply.invalid_tool_calls
            ] + [ToolMessage("Not executed: another call in this reply failed.", tool_call_id=call["id"])
                 for call in reply.tool_calls]}

        final = next((c for c in reply.tool_calls if c["name"] in self._handovers or c["name"] == OUTPUT_TOOL), None)
        if final:
            skipped = [ToolMessage("Not executed: a final result was already given.", tool_call_id=c["id"])
                       for c in reply.tool_calls if c is not final]
            try:
                if final["name"] in self._handovers:
                    handover = await self._handovers[final["name"]].call(ctx, final["args"])
                    return {"handover": handover, "messages": [
                        ToolMessage(f"Handed over to {handover.target.name}.", tool_call_id=final["id"]), *skipped]}
                parsed = self._output_tool.schema.model_validate(final["args"])
                output = parsed if parsed.__class__ is self.output_type else parsed.response
                output = await self._validate(ctx, output)
            except (ValidationError, ModelRetry) as exc:
                retry(OUTPUT_TOOL, self.retries)
                return {"retries": retries, "messages": [
                    ToolMessage(f"{_error_text(exc)}\n\nFix the errors and try again.", tool_call_id=final["id"],
                                status="error"), *skipped]}
            return {"done": True, "output": output, "messages": [
                ToolMessage("Final result processed.", tool_call_id=final["id"]), *skipped]}

        if not reply.tool_calls:
            if not self._text_output:
                retry(OUTPUT_TOOL, self.retries)
                return {"retries": retries, "messages": [HumanMessage(
                    f"Plain text responses are not permitted, please include your response in a tool call "
                    f"(the {OUTPUT_TOOL} tool, or a handover).")]}
            try:
                return {"done": True, "output": await self._validate(ctx, str(reply.text))}
            except ModelRetry as exc:
                retry(OUTPUT_TOOL, self.retries)
                return {"retries": retries, "messages": [HumanMessage(f"{exc}\n\nFix the errors and try again.")]}

        if ctx.usage.tool_calls + len(reply.tool_calls) > self.max_tool_calls:
            raise AgentError(f"{self.name}: the limit of {self.max_tool_calls} tool calls was reached")
        tools = {t.name: t for t in self._tools}
        results = []
        for call in reply.tool_calls:
            ctx.usage.tool_calls += 1
            write(StreamEvent("tool_call", self.name, name=call["name"], data=call["args"]))
            tool = tools.get(call["name"])
            try:
                if tool is None:
                    raise ModelRetry(f"Unknown tool {call['name']!r}. Tools: {', '.join(tools) or 'none'}")
                result, status = await tool.call(ctx, call["args"]), "success"
            except (ValidationError, ModelRetry) as exc:
                retry(call["name"], tool.max_retries if tool and tool.max_retries is not None else self.retries)
                result, status = f"{_error_text(exc)}\n\nFix the errors and try again.", "error"
            write(StreamEvent("tool_result", self.name, name=call["name"], data=result))
            results.append(ToolMessage(_as_text(result), tool_call_id=call["id"], name=call["name"], status=status))
        return {"retries": retries, "messages": results}

    async def _validate(self, ctx: RunContext, output: Any) -> Any:
        for validator in self._validators:
            output = await _call_with_optional_ctx(validator, ctx, output)
        return output

    # --- running ---------------------------------------------------------------------------

    def _start(self, prompt: str, deps: Any, history: Sequence[BaseMessage] | None,
               usage: Usage | None) -> tuple[Any, dict, dict]:
        graph = self.initialize()
        state = {"messages": [*(history or []), HumanMessage(prompt)], "retries": {}, "done": False,
                 "output": None, "handover": None}
        # Two steps (model, tools) per request; the request limit is checked first, with a clearer error.
        options = {"context": RunContext(deps, usage or Usage()), "config": {"recursion_limit": 2 * self.max_requests + 10}}
        return graph, state, options

    def _result(self, state: dict, usage: Usage) -> AgentResult[OutputT]:
        return AgentResult(state["output"], self.name, state["messages"], usage, self.prompt_id)

    async def run(self, prompt: str, *, deps: DepsT = None, history: Sequence[BaseMessage] | None = None,
                  usage: Usage | None = None) -> AgentResult[OutputT]:
        """Run to completion and return the final output."""
        graph, state, options = self._start(prompt, deps, history, usage)
        try:
            state = await graph.ainvoke(state, **options)
        except (GraphRecursionError, openai.APIError, TimeoutError) as exc:
            raise AgentError(f"{self.name}: {exc}") from exc
        if handover := state["handover"]:
            return await handover.target.run(handover.prompt(prompt), deps=deps, usage=options["context"].usage)
        return self._result(state, options["context"].usage)

    async def stream(self, prompt: str, *, deps: DepsT = None, history: Sequence[BaseMessage] | None = None,
                     usage: Usage | None = None) -> AsyncIterator[StreamEvent]:
        """Run and yield events as they happen: text as it's written, tool calls and results,
        handovers, and finally one "output" event carrying the AgentResult."""
        graph, state, options = self._start(prompt, deps, history, usage)
        try:
            async for mode, chunk in graph.astream(state, stream_mode=["messages", "custom", "values"], **options):
                if mode == "values":
                    state = chunk
                elif mode == "custom":
                    yield chunk  # tool calls and results, from _act
                else:
                    message, metadata = chunk
                    if metadata.get("langgraph_node") == "model" and isinstance(message, AIMessage) and message.text:
                        yield StreamEvent("text", self.name, text=str(message.text))
        except (GraphRecursionError, openai.APIError, TimeoutError) as exc:
            raise AgentError(f"{self.name}: {exc}") from exc

        if handover := state["handover"]:
            yield StreamEvent("handover", self.name, name=handover.target.name, text=handover.brief)
            async for event in handover.target.stream(handover.prompt(prompt), deps=deps, usage=options["context"].usage):
                yield event
        else:
            yield StreamEvent("output", self.name, result=self._result(state, options["context"].usage))

    def __repr__(self) -> str:
        prompt = f" ({self.prompt_id})" if self.prompt_id else ""
        return f"<{type(self).__name__} {self.name!r}{prompt}: {len(self._tools)} tools, {len(self._handovers)} handovers>"
