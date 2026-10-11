"""Order payments, step by step. Each step calls PayPal, then records what PayPal confirmed.

    create     -> draft                               price the cart and work out how it's paid
    checkout   draft            -> awaiting_payment   create the PayPal order, return its approval link
    approve    awaiting_payment -> paid | held        customer is back from PayPal: charge, or hold and
                                                      take what's due now (pay-now items, deposits)
    complete   held             -> completed          take the final amount; the rest of the hold is released
    cancel     held             -> cancelled          release the hold (anything already charged stays)
    refund     paid/held/completed                    give back part of a charge

Request ids are derived from the order, so repeating a step (a double click, a refreshed return
page) never moves money twice.
"""

import json
from datetime import date

from sqlmodel import Session, select

from app.config import APP_URL
from app.models import Business, Order, Payment, Product, Rule, utcnow
from app.payments.paypal import PayPalClient, approval_url, capture_fee_cents, cents, money
from app.payments.plan import PaymentPlan, PlanError, build_plan, final_amount_cents


class PaymentError(Exception):
    """The step isn't allowed in the order's current state (shown to the user as-is)."""


def _record(session: Session, order: Order, action: str, paypal_id: str, amount_cents: int = 0,
            status: str = "", fee_cents: int = 0, final: bool = False) -> None:
    session.add(Payment(order_id=order.id, action=action, paypal_id=paypal_id, amount_cents=amount_cents,
                        status=status, fee_cents=fee_cents, final=final))
    order.updated_at = utcnow()
    session.add(order)


def _require(order: Order, *statuses: str) -> None:
    if order.status not in statuses:
        raise PaymentError(f"Order is {order.status.replace('_', ' ')}; this needs it to be "
                           f"{' or '.join(s.replace('_', ' ') for s in statuses)}")


def create_order(session: Session, business: Business, lines: list[tuple[int, float]], *,
                 ready_by: date | None = None, note: str = "") -> Order:
    """A draft order from (product id, quantity) pairs. Raises PlanError if anything can't be ordered."""
    products = {p.id: p for p in session.exec(
        select(Product).where(Product.business_id == business.id, Product.active)).all()}
    missing = [str(product_id) for product_id, _ in lines if product_id not in products]
    if missing:
        raise PlanError(f"Not available at {business.name}: product {', '.join(missing)}")
    rules = session.exec(select(Rule).where(Rule.business_id == business.id)).all()
    plan = build_plan([(products[product_id], quantity) for product_id, quantity in lines], rules,
                      ready_by=ready_by, currency=business.currency)
    order = Order(business_id=business.id, ready_by=ready_by, note=note.strip(),
                  plan_json=json.dumps(plan.to_dict()), intent=plan.intent, hold_cents=plan.hold_cents)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def paypal_order_body(order: Order, business: Business, plan: PaymentPlan) -> dict:
    cur = plan.currency
    items = []
    for line in plan.lines:
        qty = f"{line.quantity:g} {line.unit}" if line.pricing == "per_weight" else f"{line.quantity:g}"
        items.append({"name": f"{line.name} x {qty}"[:127], "quantity": "1",
                      "unit_amount": money(line.estimate_cents, cur), "category": "PHYSICAL_GOODS"})
    buffer = plan.hold_cents - plan.estimate_cents
    if buffer:
        items.append({"name": "Extra hold for weighed items (released if not needed)", "quantity": "1",
                      "unit_amount": money(buffer, cur), "category": "PHYSICAL_GOODS"})
    return {
        "intent": plan.intent,
        "purchase_units": [{
            "reference_id": order.public_id,
            "custom_id": order.public_id,
            "description": f"{business.name} order"[:127],
            "payee": {"email_address": business.paypal_email},
            "amount": {**money(plan.hold_cents, cur), "breakdown": {"item_total": money(plan.hold_cents, cur)}},
            "items": items,
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": business.name[:127],
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": f"{APP_URL}/api/paypal/return",
            "cancel_url": f"{APP_URL}/api/paypal/cancel",
        }}},
    }


async def checkout(session: Session, client: PayPalClient, order: Order, business: Business) -> str:
    """Create the PayPal order and return the link where the customer approves it."""
    _require(order, "draft", "awaiting_payment")
    plan = PaymentPlan.from_lines(order.plan["lines"], order.plan["currency"])
    paypal_order = await client.create_order(paypal_order_body(order, business, plan),
                                             request_id=f"{order.public_id}-create")
    if order.status == "draft":
        order.status, order.paypal_order_id = "awaiting_payment", paypal_order["id"]
        _record(session, order, "create", paypal_order["id"], plan.hold_cents, paypal_order["status"])
        session.commit()
    return approval_url(paypal_order)


def _payer(order: Order, paypal_order: dict) -> None:
    payer = paypal_order.get("payer") or {}
    name = payer.get("name") or {}
    order.customer_name = " ".join(p for p in (name.get("given_name"), name.get("surname")) if p)
    order.customer_email = payer.get("email_address", "")


async def approve(session: Session, client: PayPalClient, order: Order) -> Order:
    """The customer approved at PayPal: charge (pay now), or place the hold and take what's due now."""
    if order.status in ("paid", "held", "completed"):
        return order  # already done: the return page was reloaded
    _require(order, "awaiting_payment")
    plan = order.plan

    if order.intent == "CAPTURE":
        result = await client.capture_order(order.paypal_order_id, request_id=f"{order.public_id}-capture")
        capture = result["purchase_units"][0]["payments"]["captures"][0]
        _payer(order, result)
        order.status, order.captured_cents = "paid", cents(capture["amount"])
        order.fee_cents += capture_fee_cents(capture)
        _record(session, order, "capture", capture["id"], order.captured_cents, capture["status"],
                capture_fee_cents(capture), final=True)
        session.commit()
        return order

    result = await client.authorize_order(order.paypal_order_id, request_id=f"{order.public_id}-authorize")
    authorization = result["purchase_units"][0]["payments"]["authorizations"][0]
    _payer(order, result)
    order.status, order.authorization_id = "held", authorization["id"]
    _record(session, order, "authorize", authorization["id"], cents(authorization["amount"]), authorization["status"])
    session.commit()

    due_now = plan["due_now_cents"]
    if due_now:
        await _capture(session, client, order, due_now, final=False, request_id=f"{order.public_id}-due-now")
    return order


async def _capture(session, client, order: Order, amount_cents: int, *, final: bool, request_id: str) -> None:
    capture = await client.capture_authorization(order.authorization_id, amount_cents, final=final,
                                                 request_id=request_id, currency=order.plan["currency"])
    fee = capture_fee_cents(capture)
    order.captured_cents += amount_cents
    order.fee_cents += fee
    _record(session, order, "capture", capture["id"], amount_cents, capture["status"], fee, final)
    session.commit()


async def complete(session: Session, client: PayPalClient, order: Order, final_by_line: dict[int, int]) -> Order:
    """The order is ready: charge what's left (exact amounts for weighed items) and release the rest."""
    _require(order, "held")
    plan = PaymentPlan.from_lines(order.plan["lines"], order.plan["currency"])
    amount = final_amount_cents(plan, final_by_line)
    if amount:
        await _capture(session, client, order, amount, final=True, request_id=f"{order.public_id}-final")
    else:
        await client.void_authorization(order.authorization_id, request_id=f"{order.public_id}-void")
        _record(session, order, "void", order.authorization_id, status="VOIDED")
    order.status = "completed"
    order.plan_json = json.dumps(plan.to_dict())  # now with the final amounts per line
    session.add(order)
    session.commit()
    return order


async def cancel(session: Session, client: PayPalClient, order: Order) -> Order:
    """Cancel before completion. A hold is released; anything already charged stays (refund it separately)."""
    _require(order, "draft", "awaiting_payment", "held")
    if order.status == "held":
        await client.void_authorization(order.authorization_id, request_id=f"{order.public_id}-void")
        _record(session, order, "void", order.authorization_id, status="VOIDED")
    order.status = "cancelled"
    session.add(order)
    session.commit()
    return order


async def refund(session: Session, client: PayPalClient, order: Order, capture_id: str, amount_cents: int) -> Order:
    """Give back part or all of one charge. PayPal keeps its fee on the original charge."""
    _require(order, "paid", "held", "completed", "cancelled")
    payments = session.exec(select(Payment).where(Payment.order_id == order.id)).all()
    capture = next((p for p in payments if p.action == "capture" and p.paypal_id == capture_id), None)
    if not capture:
        raise PaymentError("That charge isn't part of this order")
    earlier = [p for p in payments if p.action == "refund" and p.parent_id == capture_id]
    available = capture.amount_cents - sum(p.amount_cents for p in earlier)
    if not 0 < amount_cents <= available:
        raise PaymentError(f"You can refund between $0.01 and ${money(available)['value']} of that charge")
    # The id counts earlier refunds: a double click repeats the same refund, a later one is a new refund.
    result = await client.refund_capture(capture_id, amount_cents, currency=order.plan["currency"],
                                         request_id=f"{order.public_id}-refund-{capture_id}-{len(earlier)}")
    order.refunded_cents += amount_cents
    if order.refunded_cents >= order.captured_cents and order.status in ("paid", "completed"):
        order.status = "refunded"
    session.add(Payment(order_id=order.id, action="refund", paypal_id=result["id"], parent_id=capture_id,
                        amount_cents=amount_cents, status=result["status"]))
    order.updated_at = utcnow()
    session.add(order)
    session.commit()
    return order
