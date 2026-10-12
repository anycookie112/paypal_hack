"""Tools the customer-facing agents can call.

Every tool reads the shop from `ShopDeps` and only reads: agents can look things up, never change
anything. What a tool returns is everything the model knows about the shop, so the products'
names, descriptions and tags, plus the owner's settings, are the only source of truth.
"""

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel
from sqlmodel import Session, select

from app.ai.base import RunContext
from app.domain.money import cents_to_str
from app.domain.rules import describe_rule
from app.models import Business, Product, Rule
from app.payments.plan import PaymentPlan, PlanError, PlanLine, build_plan


@dataclass
class ShopDeps:
    """One shop's data, loaded once per conversation turn."""
    business: Business
    products: list[Product]
    rules: list[Rule]

    @classmethod
    def load(cls, session: Session, business: Business) -> "ShopDeps":
        products = session.exec(select(Product).where(Product.business_id == business.id, Product.active)).all()
        rules = session.exec(select(Rule).where(Rule.business_id == business.id)).all()
        return cls(business, list(products), list(rules))

    def product(self, product_id: int) -> Product | None:
        return next((p for p in self.products if p.id == product_id), None)


class ProductView(BaseModel):
    """A product as the model sees it."""
    id: int
    name: str
    category: str
    price: str            # "$3.00 each", "$8.99 per lb", "price on request"
    tags: list[str]
    notice_hours: int
    description: str


def product_view(p: Product) -> ProductView:
    if p.pricing == "quote":
        price = "price on request"
    else:
        price = f"{cents_to_str(p.price_cents)} {'per ' + p.unit if p.pricing == 'per_weight' else p.unit}"
    tags = [t.strip() for t in p.tags.split(",") if t.strip()]
    return ProductView(id=p.id, name=p.name, category=p.category, price=price, tags=tags,
                       notice_hours=p.lead_time_hours, description=p.description)


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower())}


# --- tools ---------------------------------------------------------------------------------------

def shop_info(ctx: RunContext[ShopDeps]) -> dict:
    """The shop's name, type and description."""
    b = ctx.deps.business
    return {"name": b.name, "type": b.kind, "description": b.description, "currency": b.currency}


def list_categories(ctx: RunContext[ShopDeps]) -> dict[str, int]:
    """Product categories and how many products each has."""
    counts: dict[str, int] = {}
    for p in ctx.deps.products:
        counts[p.category or "Other"] = counts.get(p.category or "Other", 0) + 1
    return counts


def search_products(ctx: RunContext[ShopDeps], query: str = "", category: str = "", tag: str = "",
                    max_price: float | None = None, limit: int = 15) -> list[ProductView] | str:
    """Search the shop's products. All filters are optional; the best matches come first.

    Args:
        query: Words to match against product names, descriptions and tags, e.g. "chocolate cupcakes".
        category: Only this category (see list_categories).
        tag: Only products with this tag, e.g. "gluten-free".
        max_price: Only products costing at most this many dollars per unit.
        limit: Maximum number of products to return.
    """
    wanted = _words(query)
    scored = []
    for p in ctx.deps.products:
        if category and p.category.lower() != category.strip().lower():
            continue
        if tag and tag.strip().lower() not in p.tags.lower():
            continue
        if max_price is not None and (p.pricing == "quote" or p.price_cents > max_price * 100):
            continue
        score = len(wanted & _words(f"{p.name} {p.category} {p.tags}")) * 2 + len(wanted & _words(p.description))
        if wanted and score == 0:
            continue
        scored.append((score, p.name, p))
    if not scored:
        hint = (f" No product is tagged '{tag}': don't assume any product is {tag}; add it to your questions "
                "for the shop instead." if tag else "")
        return f"No products match.{hint} Try fewer or different words, or list_categories."
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [product_view(p) for _, _, p in scored[:max(1, min(limit, 50))]]


def get_product(ctx: RunContext[ShopDeps], product_id: int) -> ProductView | str:
    """Full details of one product by id."""
    p = ctx.deps.product(product_id)
    return product_view(p) if p else f"No product with id {product_id}."


def shop_policies(ctx: RunContext[ShopDeps]) -> list[str]:
    """The shop's policies: payment, notice, minimum order, delivery, daily limits, substitutions."""
    policies = [describe_rule(r.kind, r.applies_to, r.params) for r in ctx.deps.rules]
    return policies or ["No special policies: customers pay in full when ordering."]


class CartLine(BaseModel):
    product_id: int
    quantity: float


class QuotedLine(BaseModel):
    product_id: int
    name: str
    quantity: float
    price: str     # this line's total (an estimate for items sold by weight)
    how_paid: str  # the shop's payment style for it, in plain words


class Quote(BaseModel):
    """An exact price and payment breakdown, calculated by the shop's system."""
    lines: list[QuotedLine]
    total: str
    charged_when_customer_approves: str
    held_not_charged: str
    charged_at_pickup: str
    customer_summary: list[str]  # the payment explanation the customer will see at checkout
    budget_check: str = ""


def plan_for_cart(deps: ShopDeps, lines: list[CartLine], ready_by: date | None = None) -> PaymentPlan:
    """The same pricing and payment plan checkout uses. Raises PlanError for anything invalid."""
    unknown = [str(line.product_id) for line in lines if not deps.product(line.product_id)]
    if unknown:
        raise PlanError(f"No product with id {', '.join(unknown)}: use ids from search_products")
    return build_plan([(deps.product(line.product_id), line.quantity) for line in lines], deps.rules,
                      ready_by=ready_by, currency=deps.business.currency)


def how_paid(line: PlanLine) -> str:
    if line.style == "deposit_balance":
        return (f"{line.deposit_percent}% deposit ({cents_to_str(line.due_now_cents)}) when the customer approves; "
                f"the balance ({cents_to_str(line.estimate_cents - line.due_now_cents)}) at pickup")
    if line.style == "hold_exact":
        return (f"estimate held, not charged (plus {line.buffer_percent}% for weight differences); "
                "the exact amount is charged at pickup")
    return "paid in full when the customer approves"


def price_order(ctx: RunContext[ShopDeps], lines: list[CartLine], ready_by: date | None = None,
                budget: float | None = None) -> Quote | str:
    """Price a draft order exactly and show how the customer will pay for it. Always use this for
    totals instead of adding up prices yourself, and call it again after changing the order.

    Args:
        lines: The products (by id) and quantities. For items sold by weight, quantity is the weight.
        ready_by: When the customer needs the order, if they said.
        budget: The customer's budget in dollars, if they gave one.
    """
    try:
        plan = plan_for_cart(ctx.deps, lines, ready_by)
    except PlanError as exc:
        return f"Can't price this order: {exc}"
    budget_check = ""
    if budget is not None:
        diff = round(budget * 100) - plan.estimate_cents
        budget_check = (f"within budget, {cents_to_str(diff)} to spare" if diff >= 0
                        else f"OVER budget by {cents_to_str(-diff)}: change the order")
    return Quote(
        lines=[QuotedLine(product_id=line.product_id, name=line.name, quantity=line.quantity,
                          price=cents_to_str(line.estimate_cents), how_paid=how_paid(line)) for line in plan.lines],
        total=cents_to_str(plan.estimate_cents),
        charged_when_customer_approves=cents_to_str(plan.due_now_cents),
        held_not_charged=cents_to_str(plan.hold_cents - plan.due_now_cents),
        charged_at_pickup=cents_to_str(plan.due_later_cents),
        customer_summary=plan.summary(),
        budget_check=budget_check,
    )


SHOP_TOOLS = [shop_info, list_categories, search_products, get_product, shop_policies, price_order]
