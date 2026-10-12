"""The agent base class, with a fake chat model standing in for the LLM (no network)."""

import asyncio
import json
from dataclasses import dataclass

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from app.ai import AgentError, BaseAgent, ModelRetry, RunContext, agent_prompt, agent_tool, agent_validator
from app.ai.base import _Tool
from tests.fakes import FakeChatModel, call, tool_names


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


def price_model():
    """Looks up the cookie's price, then answers with it."""
    def respond(messages, tools):
        results = [m for m in messages if isinstance(m, ToolMessage)]
        return f"A cookie is {results[-1].content}." if results else call("price_of", {"product": "cookie"})
    return FakeChatModel(respond=respond)


def answer(output: dict):
    """Always gives `output` as the final result."""
    return FakeChatModel(respond=lambda messages, tools: call("final_result", output))


def test_normal_output_calls_registered_tools_with_deps():
    agent = PriceAgent(model=price_model())
    result = run(agent.run("How much is a cookie?", deps=SHOP))
    assert agent.calls == ["cookie"]
    assert result.handled_by == "prices"
    assert result.output == "A cookie is $3.00."
    assert (result.usage.requests, result.usage.tool_calls) == (2, 1)


def test_prompts_include_static_and_dynamic_instructions():
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.extend(messages) or "ok")
    run(PriceAgent(model=model).run("hi", deps=SHOP))
    assert isinstance(seen[0], SystemMessage)
    assert "Answer price questions" in seen[0].content and "The shop is Sweet Crumb." in seen[0].content
    assert isinstance(seen[1], HumanMessage) and seen[1].content == "hi"


def test_tool_schema_comes_from_signature_and_docstring():
    def search(ctx: RunContext[Shop], query: str, limit: int = 5) -> list[str]:
        """Search the products.

        Args:
            query: Words to match,
                e.g. "chocolate".
            limit: At most this many.
        """
        return []

    spec = _Tool.from_function(search).spec()["function"]
    assert spec["name"] == "search" and spec["description"] == "Search the products."
    assert set(spec["parameters"]["properties"]) == {"query", "limit"}  # no ctx
    assert spec["parameters"]["properties"]["query"]["description"] == 'Words to match, e.g. "chocolate".'
    assert spec["parameters"]["required"] == ["query"]


def test_runtime_registration_and_structured_output():
    agent = PickAgent(model=answer({"product": "cookie", "quantity": 12}))
    agent.register_prompt("Quantities are whole numbers.")
    agent.register_tool(lambda product: "in stock", name="check_stock", description="Is a product in stock?")
    assert agent.tool_names == ["check_stock"]
    result = run(agent.run("a dozen cookies", deps=SHOP))
    assert result.output == Pick(product="cookie", quantity=12)
    with pytest.raises(RuntimeError, match="already initialized"):
        agent.register_prompt("too late")


def test_structured_output_of_a_non_model_type():
    class Picks(BaseAgent[None, list[Pick]]):
        name = "picks"
        output_type = list[Pick]

    result = run(Picks(model=answer({"response": [{"product": "cookie", "quantity": 1}]})).run("one cookie"))
    assert result.output == [Pick(product="cookie", quantity=1)]


def test_bad_tool_arguments_and_text_answers_go_back_to_the_model():
    replies = iter([call("final_result", {"product": "cookie"}),                 # missing quantity
                    "Twelve cookies.",                                            # text, not the output tool
                    call("final_result", {"product": "cookie", "quantity": 12})])
    told = []

    def respond(messages, tools):
        told.append(messages[-1])
        return next(replies)

    result = run(PickAgent(model=FakeChatModel(respond=respond)).run("a dozen cookies", deps=SHOP))
    assert result.output.quantity == 12
    assert isinstance(told[1], ToolMessage) and "quantity" in told[1].content and told[1].status == "error"
    assert "Plain text responses are not permitted" in told[2].content


def test_validator_retries_until_the_limit():
    class Checked(PickAgent):
        @agent_validator
        def no_zero(self, ctx: RunContext[Shop], output: Pick) -> Pick:
            if output.quantity == 0:
                raise ModelRetry("Quantity can't be zero.")
            return output

    with pytest.raises(AgentError, match="maximum of 2 retries"):
        run(Checked(model=answer({"product": "cookie", "quantity": 0})).run("no cookies", deps=SHOP))


def test_stream_yields_tool_calls_text_and_output():
    events = run(collect(PriceAgent(model=price_model()).stream("How much is a cookie?", deps=SHOP)))
    types = [e.type for e in events]
    assert types[0] == "tool_call" and events[0].name == "price_of" and events[0].data == {"product": "cookie"}
    assert types[1] == "tool_result" and events[1].data == "$3.00"
    assert types.count("text") > 1  # streamed word by word
    assert types[-1] == "output" and events[-1].result.output == "".join(e.text for e in events if e.type == "text")
    json.dumps([e.to_json() for e in events])  # ready to send to the browser


def triage_model(handover_tool: str):
    """A model that always hands over."""
    return FakeChatModel(respond=lambda messages, tools: call(handover_tool, {"brief": "Customer wants cookies"}))


class TriageAgent(BaseAgent[Shop, str]):
    name = "triage"
    deps_type = Shop


def test_handover_returns_target_output():
    picker = PickAgent(model=answer({"product": "cookie", "quantity": 12}))
    triage = TriageAgent(model=triage_model("handover_to_picker"))
    triage.handover_to(picker)
    result = run(triage.run("I'd like a dozen cookies", deps=SHOP))
    assert result.handled_by == "picker"
    assert result.output == Pick(product="cookie", quantity=12)
    assert result.usage.requests == 2  # shared across the handover


def test_handover_passes_brief_and_original_message_and_streams_target_events():
    seen = {}

    def picker_respond(messages, tools):
        seen["prompt"], seen["tools"] = messages[-1].content, tool_names(tools)
        return call("final_result", {"product": "cookie", "quantity": 12}, text="Twelve cookies coming up.")

    picker = PickAgent(model=FakeChatModel(respond=picker_respond))
    triage = TriageAgent(model=triage_model("handover_to_picker"))
    triage.handover_to(picker)
    events = run(collect(triage.stream("I'd like a dozen cookies", deps=SHOP)))

    assert "Customer wants cookies" in seen["prompt"] and "I'd like a dozen cookies" in seen["prompt"]
    assert seen["tools"] == ["final_result"]
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

    model = FakeChatModel(respond=lambda messages, tools: "")
    with pytest.raises(TypeError, match="needs OtherDeps"):
        TriageAgent(model=model).handover_to(Other(model=model))


def test_runaway_tool_loop_becomes_agent_error():
    agent = PriceAgent(model=FakeChatModel(respond=lambda messages, tools: call("price_of", {"product": "cookie"})))
    with pytest.raises(AgentError, match="prices"):
        run(agent.run("loop", deps=SHOP))
