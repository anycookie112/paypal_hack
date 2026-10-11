"""The agent base class, with Pydantic AI's fake models standing in for the LLM (no network)."""

import asyncio
import json
from dataclasses import dataclass

import pytest
from pydantic import BaseModel
from pydantic_ai import ModelResponse, RunContext, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from app.ai import AgentError, BaseAgent, agent_prompt, agent_tool


@dataclass
class Shop:
    name: str
    prices: dict[str, float]


class Pick(BaseModel):
    product: str
    quantity: int


class PriceAgent(BaseAgent[Shop, str]):
    name = "prices"
    description = "Answers questions about prices."
    deps_type = Shop
    instructions = "Answer price questions using the price tool."

    def __init__(self, **kwargs):
        self.calls = []
        super().__init__(**kwargs)

    @agent_prompt
    def shop_name(self, ctx: RunContext[Shop]) -> str:
        return f"The shop is {ctx.deps.name}."

    @agent_tool
    def price_of(self, ctx: RunContext[Shop], product: str) -> str:
        """Look up a product's price."""
        self.calls.append(product)
        return f"${ctx.deps.prices.get(product, 0):.2f}"


class PickAgent(BaseAgent[Shop, Pick]):
    name = "picker"
    description = "Turns a request into a product and quantity."
    deps_type = Shop
    output_type = Pick


SHOP = Shop("Sweet Crumb", {"cookie": 3.0})


def run(coro):
    return asyncio.run(coro)


async def collect(stream):
    return [event async for event in stream]


def test_normal_output_calls_registered_tools_with_deps():
    agent = PriceAgent(model=TestModel())
    result = run(agent.run("How much is a cookie?", deps=SHOP))
    assert agent.calls  # TestModel calls every tool once
    assert result.handled_by == "prices"
    assert "$" in result.output


def test_prompts_include_static_and_dynamic_instructions():
    seen = {}

    def model(messages, info: AgentInfo):
        seen["instructions"] = info.instructions
        return ModelResponse(parts=[TextPart("ok")])

    run(PriceAgent(model=FunctionModel(model)).run("hi", deps=SHOP))
    assert "Answer price questions" in seen["instructions"]
    assert "The shop is Sweet Crumb." in seen["instructions"]


def test_runtime_registration_and_structured_output():
    agent = PickAgent(model=TestModel(custom_output_args={"product": "cookie", "quantity": 12}))
    agent.register_prompt("Quantities are whole numbers.")
    agent.register_tool(lambda product: "in stock", name="check_stock", description="Is a product in stock?")
    result = run(agent.run("a dozen cookies", deps=SHOP))
    assert result.output == Pick(product="cookie", quantity=12)
    with pytest.raises(RuntimeError, match="already initialized"):
        agent.register_prompt("too late")


def test_stream_yields_tool_calls_text_and_output():
    events = run(collect(PriceAgent(model=TestModel()).stream("How much is a cookie?", deps=SHOP)))
    types = [e.type for e in events]
    assert types[0] == "tool_call" and events[0].name == "price_of"
    assert "tool_result" in types and "text" in types
    assert types[-1] == "output" and events[-1].result.output == "".join(e.text for e in events if e.type == "text")
    json.dumps([e.to_json() for e in events])  # ready to send to the browser


def triage_model(handover_tool: str):
    """A model that always hands over, in both normal and streaming mode."""
    brief = json.dumps({"brief": "Customer wants cookies"})

    def model(messages, info: AgentInfo):
        return ModelResponse(parts=[ToolCallPart(handover_tool, brief)])

    async def stream(messages, info: AgentInfo):
        yield {0: DeltaToolCall(name=handover_tool, json_args=brief)}

    return FunctionModel(model, stream_function=stream)


class TriageAgent(BaseAgent[Shop, str]):
    name = "triage"
    deps_type = Shop


def test_handover_returns_target_output():
    picker = PickAgent(model=TestModel(custom_output_args={"product": "cookie", "quantity": 12}))
    triage = TriageAgent(model=triage_model("handover_to_picker"))
    triage.handover_to(picker)
    result = run(triage.run("I'd like a dozen cookies", deps=SHOP))
    assert result.handled_by == "picker"
    assert result.output == Pick(product="cookie", quantity=12)


def test_handover_passes_brief_and_original_message_and_streams_target_events():
    seen = {}

    def picker_model(messages, info: AgentInfo):
        seen["prompt"] = messages[-1].parts[-1].content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"product": "cookie", "quantity": 12})])

    async def picker_stream(messages, info: AgentInfo):
        seen["prompt"] = messages[-1].parts[-1].content
        yield "Twelve cookies coming up. "
        yield {1: DeltaToolCall(name=info.output_tools[0].name, json_args='{"product": "cookie", "quantity": 12}')}

    picker = PickAgent(model=FunctionModel(picker_model, stream_function=picker_stream))
    triage = TriageAgent(model=triage_model("handover_to_picker"))
    triage.handover_to(picker)
    events = run(collect(triage.stream("I'd like a dozen cookies", deps=SHOP)))

    assert "Customer wants cookies" in seen["prompt"] and "I'd like a dozen cookies" in seen["prompt"]
    handover = next(e for e in events if e.type == "handover")
    assert (handover.agent, handover.name, handover.text) == ("triage", "picker", "Customer wants cookies")
    assert any(e.type == "text" and e.agent == "picker" for e in events)
    final = events[-1]
    assert final.type == "output" and final.agent == "picker"
    assert final.to_json()["data"] == {"product": "cookie", "quantity": 12}


def test_handover_requires_matching_deps():
    class OtherDeps:
        pass

    class Other(BaseAgent[OtherDeps, str]):
        name = "other"
        deps_type = OtherDeps

    with pytest.raises(TypeError, match="needs OtherDeps"):
        TriageAgent(model=TestModel()).handover_to(Other(model=TestModel()))


def test_runaway_tool_loop_becomes_agent_error():
    def always_call_tool(messages, info: AgentInfo):
        return ModelResponse(parts=[ToolCallPart("price_of", {"product": "cookie"})])

    agent = PriceAgent(model=FunctionModel(always_call_tool))
    with pytest.raises(AgentError, match="prices"):
        run(agent.run("loop", deps=SHOP))
