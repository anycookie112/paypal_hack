"""How an order is paid: plain code, from the products and the owner's payment settings.

One PayPal order covers the whole cart, even when its products use different payment styles:

    style            held at approval        charged at approval     charged at pickup
    pay_now          the price               the price               -
    deposit_balance  the price               deposit %               the rest
    hold_exact       estimate + buffer %     -                       the exact final amount

If every line is pay_now, the order is a plain charge (intent=CAPTURE). Otherwise it's a hold
(intent=AUTHORIZE): we capture what's due right away, and the rest when the owner completes the
order; the final capture releases whatever wasn't needed.
"""

import math
from dataclasses import asdict, dataclass
from datetime import date

from app.domain.money import cents_to_str
from app.domain.rules import payment_style_for

# PayPal keeps an authorization for 29 days; within the first 3 ("honor period") the funds are guaranteed.
HOLD_MAX_DAYS = 29


class PlanError(ValueError):
    pass


@dataclass
class PlanLine:
    product_id: int
    name: str
    category: str
    pricing: str
    unit: str
    quantity: float
    unit_price_cents: int
    estimate_cents: int        # price x quantity
    style: str                 # pay_now | deposit_balance | hold_exact
    hold_cents: int            # authorized for this line
    due_now_cents: int         # captured right after the customer approves
    deposit_percent: int | None = None
    buffer_percent: int | None = None
    final_cents: int | None = None  # set when the owner completes the order (e.g. the weighed amount)

    @property
    def due_later_cents(self) -> int:
        """What's charged at pickup: the recorded final amount, or else the estimate, minus what was charged."""
        if self.style == "pay_now":
            return 0
        total = self.final_cents if self.final_cents is not None else self.estimate_cents
        return max(total - self.due_now_cents, 0)


@dataclass
class PaymentPlan:
    lines: list[PlanLine]
    currency: str = "USD"

    @property
    def intent(self) -> str:
        return "CAPTURE" if all(line.style == "pay_now" for line in self.lines) else "AUTHORIZE"

    @property
    def estimate_cents(self) -> int:
        return sum(line.estimate_cents for line in self.lines)

    @property
    def hold_cents(self) -> int:
        return sum(line.hold_cents for line in self.lines)

    @property
    def due_now_cents(self) -> int:
        return sum(line.due_now_cents for line in self.lines)

    @property
    def due_later_cents(self) -> int:
        return sum(line.due_later_cents for line in self.lines)

    def summary(self) -> list[str]:
        """What the customer is told before approving, generated from the numbers."""
        if self.intent == "CAPTURE":
            return [f"You pay {cents_to_str(self.hold_cents)} now."]
        out = []
        if self.due_now_cents:
            out.append(f"{cents_to_str(self.due_now_cents)} is charged when you approve.")
        out.append(f"{cents_to_str(self.hold_cents - self.due_now_cents)} is held on your PayPal account, not charged.")
        exact = [line.name for line in self.lines if line.style == "hold_exact"]
        balance = [line.name for line in self.lines if line.style == "deposit_balance"]
        if balance:
            out.append(f"The balance for {', '.join(balance)} is charged at pickup.")
        if exact:
            out.append(f"{', '.join(exact)}: you're charged the exact final amount at pickup; "
                       "any unused part of the hold is released.")
        return out

    def to_dict(self) -> dict:
        return {"intent": self.intent, "currency": self.currency, "estimate_cents": self.estimate_cents,
                "hold_cents": self.hold_cents, "due_now_cents": self.due_now_cents,
                "due_later_cents": self.due_later_cents, "summary": self.summary(),
                "lines": [{**asdict(line), "due_later_cents": line.due_later_cents} for line in self.lines]}

    @classmethod
    def from_lines(cls, lines: list[dict], currency: str = "USD") -> "PaymentPlan":
        fields = PlanLine.__dataclass_fields__
        return cls([PlanLine(**{k: v for k, v in line.items() if k in fields}) for line in lines], currency)


def plan_line(product, quantity: float, rules) -> PlanLine:
    if quantity <= 0:
        raise PlanError(f"{product.name}: quantity must be more than 0")
    if product.pricing == "quote":
        raise PlanError(f"{product.name} is priced on request: ask the shop for a quote first")
    if product.pricing != "per_weight" and quantity != int(quantity):
        raise PlanError(f"{product.name}: quantity must be a whole number")

    estimate = round(product.price_cents * quantity)
    payment = payment_style_for(rules, product.category)
    style = payment["style"]
    line = PlanLine(product.id, product.name, product.category, product.pricing, product.unit, quantity,
                    product.price_cents, estimate, style, hold_cents=estimate, due_now_cents=0)
    if style == "pay_now":
        line.due_now_cents = estimate
    elif style == "deposit_balance":
        line.deposit_percent = payment["deposit_percent"]
        line.due_now_cents = (estimate * line.deposit_percent + 50) // 100
    elif style == "hold_exact":
        line.buffer_percent = payment.get("buffer_percent", 0)
        line.hold_cents = estimate + math.ceil(estimate * line.buffer_percent / 100)
    return line


def build_plan(items: list[tuple], rules, *, ready_by: date | None = None, today: date | None = None,
               currency: str = "USD") -> PaymentPlan:
    """`items` are (Product, quantity) pairs, already checked to belong to the shop."""
    if not items:
        raise PlanError("The order is empty")
    plan = PaymentPlan([plan_line(product, quantity, rules) for product, quantity in items], currency)
    if plan.hold_cents <= 0:
        raise PlanError("The order total must be more than $0")
    days_ahead = (ready_by - (today or date.today())).days if ready_by else 0
    if plan.intent == "AUTHORIZE" and days_ahead > HOLD_MAX_DAYS:
        raise PlanError(f"Orders paid at pickup can be placed at most {HOLD_MAX_DAYS} days ahead, "
                        "because a PayPal hold expires after that")
    return plan


def final_amount_cents(plan: PaymentPlan, final_by_line: dict[int, int]) -> int:
    """What to capture when the owner completes the order. `final_by_line` gives the exact amount for
    lines whose price changed (e.g. weighed items), by line index. Never more than what's still held."""
    for index, value in final_by_line.items():
        if not 0 <= index < len(plan.lines):
            raise PlanError(f"No line {index}")
        if value < 0:
            raise PlanError("Final amounts can't be negative")
        plan.lines[index].final_cents = value
    amount = plan.due_later_cents
    still_held = plan.hold_cents - plan.due_now_cents
    if amount > still_held:
        raise PlanError(f"The final amount {cents_to_str(amount)} is more than the {cents_to_str(still_held)} "
                        "still held. Charge the difference separately.")
    return amount
