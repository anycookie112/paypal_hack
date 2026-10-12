"""The customer-facing agents.

    assistant = customer_assistant()
    async for event in assistant.stream("Party for 20 kids, $120", deps=ShopDeps.load(session, business)):
        ...

The front desk answers questions and hands ordering requests to the order builder. The order
builder only proposes: its OrderProposal is checked by code (products exist, prices, budget, rules)
before the customer sees a cart.
"""

from datetime import date
from typing import Generic, TypeVar

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from app.ai.base import BaseAgent, ModelRetry, RunContext, agent_prompt, agent_validator
from app.ai.tools import SHOP_TOOLS, CartLine, ShopDeps, plan_for_cart, shop_info, shop_policies
from app.domain.money import cents_to_str
from app.payments.plan import PlanError

OutputT = TypeVar("OutputT")


class OrderLine(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    reason: str = Field("", description="Why this product, in a few words, e.g. 'one per child'.")


class OrderProposal(BaseModel):
    lines: list[OrderLine]
    ready_by: date | None = Field(None, description="When the customer needs it, if they said.")
    explanation: str = Field(description="One or two short sentences for the customer.")
    questions: list[str] = Field(default_factory=list,
                                 description="What the shop or customer still needs to confirm.")
    budget: float | None = Field(None, description="The customer's budget in dollars, if they gave one.")


class ShopAgent(BaseAgent[ShopDeps, OutputT], Generic[OutputT]):
    """Shared by every customer-facing agent: tells the model which shop it works for, and the date."""
    deps_type = ShopDeps

    @agent_prompt
    def shop_context(self, ctx: RunContext[ShopDeps]) -> str:
        b = ctx.deps.business
        kind = f", a {b.kind}" if b.kind else ""
        return f"You work for {b.name}{kind}. Today is {date.today():%A, %B %d, %Y}. Prices are in {b.currency}."


class FrontDesk(ShopAgent[str]):
    name = "front_desk"
    description = "Greets customers, answers questions about the shop, and routes ordering requests."
    prompt = "front_desk"

    def setup(self) -> None:
        for tool in (shop_info, shop_policies):
            self.register_tool(tool)


class OrderBuilder(ShopAgent[OrderProposal]):
    name = "order_builder"
    description = "Turns a customer's request (needs, budget, date, dietary limits) into an order from the shop's products."
    output_type = OrderProposal
    prompt = "order_builder"

    def setup(self) -> None:
        for tool in SHOP_TOOLS:
            self.register_tool(tool)

    @agent_validator
    def check_order(self, ctx: RunContext[ShopDeps], output: OrderProposal) -> OrderProposal:
        """Code has the last word: the order must price (real products, valid quantities, a hold that
        PayPal allows) and fit the budget. Otherwise the model is told why and tries again."""
        if not output.lines:
            if not output.questions:
                raise ModelRetry("An order with no lines must say in `questions` what you need to know.")
            return output
        try:
            plan = plan_for_cart(ctx.deps, [CartLine(product_id=line.product_id, quantity=line.quantity)
                                            for line in output.lines], output.ready_by)
        except PlanError as exc:
            raise ModelRetry(f"{exc}. Fix the order.")
        if output.budget is not None and plan.estimate_cents > round(output.budget * 100):
            raise ModelRetry(f"The order totals {cents_to_str(plan.estimate_cents)}, over the "
                             f"{cents_to_str(round(output.budget * 100))} budget. Change it and check with price_order.")
        return output


def customer_assistant(*, model: BaseChatModel | None = None) -> FrontDesk:
    """The front desk, able to hand over to the order builder."""
    front_desk = FrontDesk(model=model)
    front_desk.handover_to(OrderBuilder(model=model))
    return front_desk
