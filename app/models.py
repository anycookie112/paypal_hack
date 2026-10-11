import json
import secrets
from datetime import date, datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow():
    return datetime.now(timezone.utc)


class Business(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    kind: str = ""            # bakery, grocer, florist, ...
    description: str = ""
    paypal_email: str = ""    # PayPal account that receives this business's payments
    currency: str = "USD"
    created_at: datetime = Field(default_factory=utcnow)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    name: str
    category: str = ""
    description: str = ""
    price_cents: int = 0
    pricing: str = "fixed"    # fixed | per_weight | quote
    unit: str = "each"        # each, lb, kg, dozen, ...
    tags: str = ""            # comma-separated: vegan, nut-free, gluten-free, ...
    lead_time_hours: int = 0
    active: bool = True


class Rule(SQLModel, table=True):
    """A business rule in structured form. Code enforces it; `source_text` is what the owner wrote."""
    id: int | None = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    kind: str                 # see app.domain.rules.RULE_KINDS
    applies_to: str = ""      # "" = whole business, otherwise a product category
    params_json: str = "{}"
    source_text: str = ""

    @property
    def params(self) -> dict:
        return json.loads(self.params_json or "{}")


class Order(SQLModel, table=True):
    """A customer's order and where its payment stands. Amounts are what PayPal confirmed."""
    id: int | None = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=lambda: secrets.token_urlsafe(9), index=True, unique=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    # draft -> awaiting_payment -> paid (pay now)            -> refunded
    #                           -> held -> completed          -> refunded
    #                                   -> cancelled (hold released)
    status: str = "draft"
    ready_by: date | None = None
    note: str = ""
    plan_json: str = "{}"           # app.payments.plan.PaymentPlan.to_dict()
    intent: str = ""                # CAPTURE | AUTHORIZE
    hold_cents: int = 0             # authorized (or charged, for CAPTURE)
    captured_cents: int = 0
    refunded_cents: int = 0
    fee_cents: int = 0              # PayPal fees on captures (not returned on refunds)
    paypal_order_id: str = Field(default="", index=True)
    authorization_id: str = ""
    customer_name: str = ""
    customer_email: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def plan(self) -> dict:
        return json.loads(self.plan_json or "{}")


class Payment(SQLModel, table=True):
    """Every PayPal call that changed an order's money, in order: the audit trail."""
    id: int | None = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id", index=True)
    action: str                     # create | capture | authorize | void | refund
    paypal_id: str = ""             # order, authorization, capture or refund id
    parent_id: str = ""             # for a refund: the capture it refunds
    amount_cents: int = 0
    fee_cents: int = 0
    status: str = ""                # PayPal's status for it
    final: bool = False             # capture with final_capture=true (released the rest of the hold)
    created_at: datetime = Field(default_factory=utcnow)
