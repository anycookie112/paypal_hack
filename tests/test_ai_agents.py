"""Prompts, tools and the customer agents, with fake models (no network)."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlmodel import Session, select

from app.ai import prompts
from app.ai.agents import FrontDesk, OrderBuilder, OrderProposal, customer_assistant
from app.ai.tools import ShopDeps, get_product, list_categories, search_products, shop_policies
from app.db import engine
from app.models import Business, Product, Rule

BAKERY = Business(id=1, slug="sweet-crumb", name="Sweet Crumb", kind="bakery", paypal_email="b@example.com")
PRODUCTS = [
    Product(id=1, business_id=1, name="Vanilla cupcakes", category="Cupcakes", price_cents=999, unit="12 ct",
            tags="nut-free", description="Bakery Fresh, 12 ct"),
    Product(id=2, business_id=1, name="Chocolate cupcakes", category="Cupcakes", price_cents=1099, unit="12 ct"),
    Product(id=3, business_id=1, name="Sugar cookies", category="Cookies", price_cents=300, unit="12 ct"),
    Product(id=4, business_id=1, name="Wedding cake", category="Cakes", pricing="quote", lead_time_hours=336),
    Product(id=5, business_id=1, name="Rye bread", category="Bread", pricing="per_weight", price_cents=450, unit="lb"),
]
RULES = [Rule(id=1, business_id=1, kind="lead_time", applies_to="", params_json='{"hours": 24}')]
DEPS = ShopDeps(BAKERY, PRODUCTS, RULES)
CTX = SimpleNamespace(deps=DEPS)


# --- prompts -------------------------------------------------------------------------------------

def test_latest_prompt_by_default_and_pinning(monkeypatch):
    assert prompts.get("front_desk").version == max(p.version for p in prompts.history("front_desk"))
    assert prompts.get("front_desk", 1).id == "front_desk@v1"
    monkeypatch.setenv("PROMPT_VERSIONS", "order_builder=1, front_desk=1")
    assert prompts.get("front_desk").version == 1
    with pytest.raises(KeyError, match="no v99"):
        prompts.get("front_desk", 99)


def test_versions_are_append_only():
    with pytest.raises(ValueError, match="already defined"):
        prompts.define("front_desk", 1, "changed", changes="")
    with pytest.raises(ValueError, match="next version"):
        prompts.define("front_desk", 9, "skipped", changes="")
    assert all(p.changes for p in prompts.history("front_desk"))


def test_agent_uses_and_records_its_prompt_version():
    seen = {}

    def model(messages, info: AgentInfo):
        seen["instructions"] = info.instructions
        return ModelResponse(parts=[TextPart("hello")])

    result = asyncio.run(FrontDesk(model=FunctionModel(model), prompt_version=1).run("hi", deps=DEPS))
    assert result.prompt_id == "front_desk@v1"
    assert prompts.get("front_desk", 1).text in seen["instructions"]
    assert "Never guess" not in seen["instructions"]  # only in v2
    assert "You work for Sweet Crumb, a bakery." in seen["instructions"]


# --- tools ---------------------------------------------------------------------------------------

def test_search_ranks_name_matches_and_filters():
    names = [p.name for p in search_products(CTX, query="vanilla cupcake")]
    assert names[0] == "Vanilla cupcakes" and "Sugar cookies" not in names
    assert [p.name for p in search_products(CTX, tag="nut-free")] == ["Vanilla cupcakes"]
    cheap = [p.name for p in search_products(CTX, max_price=5)]
    assert "Sugar cookies" in cheap and "Wedding cake" not in cheap  # price on request never counts as cheap
    assert [p.name for p in search_products(CTX, category="cakes")] == ["Wedding cake"]


def test_product_view_states_prices_plainly():
    assert get_product(CTX, 3).price == "$3.00 12 ct"
    assert get_product(CTX, 4).price == "price on request" and get_product(CTX, 4).notice_hours == 336
    assert get_product(CTX, 5).price == "$4.50 per lb"
    assert get_product(CTX, 99) == "No product with id 99."
    assert get_product(CTX, 1).tags == ["nut-free"]


def test_categories_and_policies():
    assert list_categories(CTX) == {"Cupcakes": 2, "Cookies": 1, "Cakes": 1, "Bread": 1}
    assert shop_policies(CTX) == ["All orders: at least 1 day's notice"]


def test_shop_deps_load_only_active_products(client, bakery):
    client.post(f"/api/businesses/{bakery}/products", json={"name": "Scone", "price": "3", "pricing": "fixed"})
    with Session(engine) as session:
        business = session.exec(select(Business).where(Business.slug == bakery)).one()
        hidden = Product(business_id=business.id, name="Old item", price_cents=100, active=False)
        session.add(hidden)
        session.commit()
        deps = ShopDeps.load(session, business)
    assert [p.name for p in deps.products] == ["Scone"]
    assert {r.kind for r in deps.rules} == {"payment", "substitution", "lead_time"}


# --- customer assistant --------------------------------------------------------------------------

def test_front_desk_hands_ordering_to_order_builder():
    def model(messages, info: AgentInfo):
        names = [t.name for t in info.output_tools]
        if "handover_to_order_builder" in names:
            return ModelResponse(parts=[ToolCallPart("handover_to_order_builder", {"brief": "Party for 20 kids"})])
        proposal = {"lines": [{"product_id": 1, "quantity": 2}], "explanation": "Two dozen cupcakes."}
        return ModelResponse(parts=[ToolCallPart(names[0], proposal)])

    result = asyncio.run(customer_assistant(model=FunctionModel(model)).run("Party for 20 kids", deps=DEPS))
    assert result.handled_by == "order_builder" and result.prompt_id.startswith("order_builder@v")
    assert isinstance(result.output, OrderProposal) and result.output.lines[0].product_id == 1


def test_order_builder_has_every_shop_tool():
    assert set(OrderBuilder().tool_names) == {"shop_info", "list_categories", "search_products", "get_product",
                                              "shop_policies", "price_order"}


def test_empty_search_tells_the_model_not_to_assume_tags():
    reply = search_products(CTX, query="brownies", tag="vegan")
    assert isinstance(reply, str) and "don't assume any product is vegan" in reply


def test_agents_cap_reply_length_and_parallel_tool_calls():
    seen = {}

    def model(messages, info: AgentInfo):
        seen.update(info.model_settings or {})
        return ModelResponse(parts=[TextPart("hi")])

    asyncio.run(FrontDesk(model=FunctionModel(model)).run("hi", deps=DEPS))
    assert seen["max_tokens"] == FrontDesk.max_reply_tokens and seen["parallel_tool_calls"] is False


def test_stream_events_serialize_tool_results():
    from app.ai.base import StreamEvent
    event = StreamEvent("tool_result", "order_builder", name="search_products", data=search_products(CTX, query="cookies"))
    assert event.to_json()["data"][0]["name"] == "Sugar cookies"


# --- billing: price_order and the order check ------------------------------------------------------

BILLING_RULES = [
    Rule(id=2, business_id=1, kind="payment", applies_to="Cakes",
         params_json='{"style": "deposit_balance", "deposit_percent": 50}'),
    Rule(id=3, business_id=1, kind="payment", applies_to="Bread",
         params_json='{"style": "hold_exact", "buffer_percent": 10}'),
]
CAKE = Product(id=6, business_id=1, name="Custom cake 6 inch", category="Cakes", price_cents=4000)
BILLING = SimpleNamespace(deps=ShopDeps(BAKERY, PRODUCTS + [CAKE], BILLING_RULES))


def test_price_order_explains_each_payment_style():
    from app.ai.tools import CartLine, price_order
    quote = price_order(BILLING, [CartLine(product_id=1, quantity=2), CartLine(product_id=6, quantity=1),
                                  CartLine(product_id=5, quantity=2)], budget=70)
    assert quote.total == "$68.98"                       # 2 x 9.99 + 40 + 2 lb x 4.50
    assert quote.charged_when_customer_approves == "$39.98"  # cupcakes + 50% deposit
    assert quote.held_not_charged == "$29.90"            # cake balance + bread estimate + 10%
    assert [line.how_paid.split(" ")[0] for line in quote.lines] == ["paid", "50%", "estimate"]
    assert quote.budget_check == "within budget, $1.02 to spare"
    assert "OVER budget" in price_order(BILLING, [CartLine(product_id=6, quantity=2)], budget=50).budget_check
    assert "No product with id 99" in price_order(BILLING, [CartLine(product_id=99, quantity=1)])
    assert "priced on request" in price_order(BILLING, [CartLine(product_id=4, quantity=1)])


def proposal_model(*outputs):
    """A fake order builder that returns `outputs` in turn, recording what it's told after each."""
    told = []

    def model(messages, info: AgentInfo):
        told.append(messages[-1].parts[-1])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, outputs[len(told) - 1])])
    return FunctionModel(model), told


def test_order_check_makes_the_model_fix_unknown_products_and_budget():
    bad_product = {"lines": [{"product_id": 99, "quantity": 1}], "explanation": "x"}
    over_budget = {"lines": [{"product_id": 6, "quantity": 2}], "explanation": "x", "budget": 50}
    good = {"lines": [{"product_id": 6, "quantity": 1}], "explanation": "One cake.", "budget": 50}
    model, told = proposal_model(bad_product, over_budget, good)
    builder = OrderBuilder(model=model)
    builder.retries = 3
    result = asyncio.run(builder.run("cake please, $50", deps=BILLING.deps))
    assert result.output.lines[0].product_id == 6
    assert "No product with id 99" in told[1].content
    assert "over the $50.00 budget" in told[2].content


def test_order_with_no_lines_must_ask_something():
    model, told = proposal_model({"lines": [], "explanation": "?"},
                                 {"lines": [], "explanation": "?", "questions": ["How many guests?"]})
    result = asyncio.run(OrderBuilder(model=model).run("a party", deps=BILLING.deps))
    assert result.output.questions == ["How many guests?"] and "questions" in told[1].content
