"""Base class for the app's AI agents, on top of Pydantic AI.

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

The Pydantic AI agent is built on first use (`initialize()`), so prompts, tools and handovers can
also be registered at runtime with `register_prompt`, `register_tool` and `handover_to` until then.

A handover passes the conversation to another agent, which produces the final output in its place:
`triage.handover_to(order_agent)` lets the triage agent decide "this is an order" and hand over.
The model only makes the decision (by calling a handover output tool with a brief); this class then
runs the target, so the target's events stream live.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic_ai import (
    Agent,
    ModelSettings,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    RunUsage,
    TextPart,
    TextPartDelta,
    Tool,
    ToolOutput,
    UsageLimits,
)
from pydantic_ai.exceptions import AgentRunError
from pydantic_core import to_jsonable_python
from pydantic_ai.models import Model

from app.ai import prompts
from app.ai.model import AgentError, default_model

DepsT = TypeVar("DepsT")
OutputT = TypeVar("OutputT")

_TOOL_MARK = "__agent_tool__"
_PROMPT_MARK = "__agent_prompt__"
_VALIDATOR_MARK = "__agent_validator__"


def agent_tool(func: Callable | None = None, *, name: str | None = None, retries: int | None = None):
    """Mark a method as a tool the model can call. The docstring is the tool's description; take
    `ctx: RunContext[Deps]` as the first parameter to read the run's dependencies."""
    def mark(f: Callable) -> Callable:
        setattr(f, _TOOL_MARK, {"name": name, "retries": retries})
        return f
    return mark(func) if func else mark


def agent_prompt(func: Callable) -> Callable:
    """Mark a method as instructions computed for every run, e.g. from `ctx.deps`."""
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
    messages: list[ModelMessage]
    usage: RunUsage
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
    """What a handover output tool returns: this agent's run ends and `target` takes over."""
    target: "BaseAgent"
    brief: str

    def prompt(self, original: str) -> str:
        return f"{self.brief}\n\nThe user's original message:\n{original}"


def _skip_handovers(validator: Callable) -> Callable:
    def validate(ctx: RunContext[Any], output: Any) -> Any:
        return output if isinstance(output, _Handover) else validator(ctx, output)
    return validate


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

    def __init__(self, *, model: Model | None = None, prompt_version: int | None = None):
        """`model` overrides the configured LLM (tests pass Pydantic AI's TestModel or FunctionModel).
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
        self._tools: list[Tool] = []
        self._handovers: list[ToolOutput] = []
        self._validators: list[Callable] = []
        self._agent: Agent | None = None
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
        self._tools.append(Tool(func, name=name, description=description, max_retries=retries))

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

        def handover(brief: str) -> Any:  # a _Handover; Any because it has no JSON schema
            """
            Args:
                brief: What the next agent needs to know: the request and anything already established.
            """
            return _Handover(target, brief)

        self._handovers.append(ToolOutput(
            handover, name=name or f"handover_to_{target.name}",
            description=description or f"Hand over to the {target.name} agent: {target.description}"))

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
        if self._agent is not None:
            raise RuntimeError(f"{self.name} is already initialized; register prompts, tools and handovers first")

    # --- running ---------------------------------------------------------------------------

    def initialize(self) -> Agent:
        """Build the Pydantic AI agent from everything registered. Runs automatically on first use."""
        if self._agent is None:
            output_type: Any = [self.output_type, *self._handovers] if self._handovers else self.output_type
            self._agent = Agent(
                self._model or default_model(),
                name=self.name,
                deps_type=self.deps_type,
                output_type=output_type,
                instructions=list(self._prompts) or None,
                tools=self._tools,
                retries=self.retries,
                model_settings=ModelSettings(max_tokens=self.max_reply_tokens,
                                             **({"parallel_tool_calls": False} if self._tools and not self.parallel_tool_calls else {})),
            )
            for validator in self._validators:
                self._agent.output_validator(_skip_handovers(validator))
        return self._agent

    def _limits(self) -> UsageLimits:
        return UsageLimits(request_limit=self.max_requests, tool_calls_limit=self.max_tool_calls)

    async def run(self, prompt: str, *, deps: DepsT = None, history: Sequence[ModelMessage] | None = None,
                  usage: RunUsage | None = None) -> AgentResult[OutputT]:
        """Run to completion and return the final output."""
        agent = self.initialize()
        try:
            result = await agent.run(prompt, deps=deps, message_history=history, usage=usage,
                                     usage_limits=self._limits())
        except AgentRunError as exc:
            raise AgentError(f"{self.name}: {exc}") from exc
        if isinstance(result.output, _Handover):
            handover = result.output
            return await handover.target.run(handover.prompt(prompt), deps=deps, usage=result.usage)
        return AgentResult(result.output, self.name, result.all_messages(), result.usage, self.prompt_id)

    async def stream(self, prompt: str, *, deps: DepsT = None, history: Sequence[ModelMessage] | None = None,
                     usage: RunUsage | None = None) -> AsyncIterator[StreamEvent]:
        """Run and yield events as they happen: text as it's written, tool calls and results,
        handovers, and finally one "output" event carrying the AgentResult."""
        agent = self.initialize()
        handover, usage_so_far = None, None
        try:
            async with agent.run_stream_events(prompt, deps=deps, message_history=history, usage=usage,
                                               usage_limits=self._limits()) as events:
                async for event in events:
                    if isinstance(event, AgentRunResultEvent) and isinstance(event.result.output, _Handover):
                        handover, usage_so_far = event.result.output, event.result.usage
                        continue
                    translated = self._translate(event)
                    if translated:
                        yield translated
        except AgentRunError as exc:
            raise AgentError(f"{self.name}: {exc}") from exc

        if handover:
            yield StreamEvent("handover", self.name, name=handover.target.name, text=handover.brief)
            async for event in handover.target.stream(handover.prompt(prompt), deps=deps, usage=usage_so_far):
                yield event

    def _translate(self, event: Any) -> StreamEvent | None:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart) and event.part.content:
            return StreamEvent("text", self.name, text=event.part.content)
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            return StreamEvent("text", self.name, text=event.delta.content_delta)
        if isinstance(event, FunctionToolCallEvent):
            return StreamEvent("tool_call", self.name, name=event.part.tool_name, data=event.part.args_as_dict())
        if isinstance(event, FunctionToolResultEvent):
            return StreamEvent("tool_result", self.name, name=event.part.tool_name, data=event.part.content)
        if isinstance(event, AgentRunResultEvent):
            result = AgentResult(event.result.output, self.name, event.result.all_messages(), event.result.usage,
                                 self.prompt_id)
            return StreamEvent("output", self.name, result=result)
        return None

    def __repr__(self) -> str:
        prompt = f" ({self.prompt_id})" if self.prompt_id else ""
        return f"<{type(self).__name__} {self.name!r}{prompt}: {len(self._tools)} tools, {len(self._handovers)} handovers>"
