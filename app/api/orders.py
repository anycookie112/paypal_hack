"""Orders and their payments: create from a cart, pay through PayPal, then complete, cancel or refund.

The owner actions (complete, cancel, refund) aren't behind a login yet: the prototype has no accounts.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.common import get_business
from app.config import APP_URL
from app.db import get_session
from app.domain.money import to_cents
from app.models import Business, Order, Payment
from app.payments import service
from app.payments.paypal import PayPalClient, PayPalError
from app.payments.plan import PlanError

router = APIRouter()

_paypal = PayPalClient()


def paypal_client() -> PayPalClient:
    """A dependency, so tests can swap in a fake PayPal."""
    return _paypal


class LineIn(BaseModel):
    product_id: int
    quantity: float


class OrderIn(BaseModel):
    lines: list[LineIn]
    ready_by: date | None = None
    note: str = ""


class CompleteIn(BaseModel):
    final_amounts: dict[int, str] = {}  # line index -> exact amount in dollars, e.g. for weighed items


class RefundIn(BaseModel):
    capture_id: str
    amount: str


def get_order(session: Session, public_id: str) -> Order:
    order = session.exec(select(Order).where(Order.public_id == public_id)).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return order


def order_out(session: Session, order: Order) -> dict:
    business = session.get(Business, order.business_id)
    payments = session.exec(select(Payment).where(Payment.order_id == order.id).order_by(Payment.id)).all()
    return {
        "public_id": order.public_id, "status": order.status, "ready_by": order.ready_by, "note": order.note,
        "business": {"slug": business.slug, "name": business.name},
        "plan": order.plan, "captured_cents": order.captured_cents, "refunded_cents": order.refunded_cents,
        "fee_cents": order.fee_cents, "customer_name": order.customer_name, "customer_email": order.customer_email,
        "created_at": order.created_at,
        "payments": [p.model_dump(exclude={"order_id"}) for p in payments],
    }


async def run_step(session: Session, order: Order, step) -> dict:
    """Run a payment step and turn its failures into HTTP errors."""
    try:
        await step
    except service.PaymentError as exc:
        raise HTTPException(409, str(exc))
    except PlanError as exc:
        raise HTTPException(422, str(exc))
    except PayPalError as exc:
        session.rollback()
        raise HTTPException(502, f"{exc} (PayPal debug id {exc.debug_id})" if exc.debug_id else str(exc))
    return order_out(session, order)


@router.post("/businesses/{slug}/orders", status_code=201)
def create_order(slug: str, body: OrderIn, session: Session = Depends(get_session)):
    """Price a cart and work out how it's paid. Nothing is charged until checkout."""
    business = get_business(session, slug)
    try:
        order = service.create_order(session, business, [(line.product_id, line.quantity) for line in body.lines],
                                     ready_by=body.ready_by, note=body.note)
    except PlanError as exc:
        raise HTTPException(422, str(exc))
    return order_out(session, order)


@router.get("/businesses/{slug}/orders")
def list_orders(slug: str, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    orders = session.exec(select(Order).where(Order.business_id == business.id).order_by(Order.id.desc())).all()
    return [order_out(session, o) for o in orders]


@router.get("/orders/{public_id}")
def read_order(public_id: str, session: Session = Depends(get_session)):
    return order_out(session, get_order(session, public_id))


@router.post("/orders/{public_id}/checkout")
async def checkout(public_id: str, session: Session = Depends(get_session),
                   client: PayPalClient = Depends(paypal_client)):
    """Create the PayPal order; the customer is sent to the returned link to approve it."""
    order = get_order(session, public_id)
    business = session.get(Business, order.business_id)
    try:
        url = await service.checkout(session, client, order, business)
    except service.PaymentError as exc:
        raise HTTPException(409, str(exc))
    except PayPalError as exc:
        raise HTTPException(502, str(exc))
    return {"approval_url": url}


def _order_page(session: Session, order: Order, payment: str = "") -> RedirectResponse:
    slug = session.get(Business, order.business_id).slug
    query = f"?payment={payment}" if payment else ""
    return RedirectResponse(f"{APP_URL}/b/{slug}/orders/{order.public_id}{query}", 303)


@router.get("/paypal/return")
async def paypal_return(token: str, session: Session = Depends(get_session),
                        client: PayPalClient = Depends(paypal_client)):
    """PayPal sends the customer here after they approve (`token` is the PayPal order id)."""
    order = session.exec(select(Order).where(Order.paypal_order_id == token)).first()
    if not order:
        raise HTTPException(404, "Unknown PayPal order")
    try:
        await service.approve(session, client, order)
    except (service.PaymentError, PayPalError):
        session.rollback()
        return _order_page(session, order, "failed")
    return _order_page(session, order)


@router.get("/paypal/cancel")
def paypal_cancel(token: str, session: Session = Depends(get_session)):
    """The customer clicked cancel at PayPal. Nothing was charged; they can check out again."""
    order = session.exec(select(Order).where(Order.paypal_order_id == token)).first()
    if not order:
        raise HTTPException(404, "Unknown PayPal order")
    return _order_page(session, order, "cancelled")


@router.post("/orders/{public_id}/complete")
async def complete(public_id: str, body: CompleteIn, session: Session = Depends(get_session),
                   client: PayPalClient = Depends(paypal_client)):
    """Owner: the order is ready. Charge what's left (exact amounts for weighed items), release the rest."""
    order = get_order(session, public_id)
    try:
        final = {int(i): to_cents(v) for i, v in body.final_amounts.items()}
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return await run_step(session, order, service.complete(session, client, order, final))


@router.post("/orders/{public_id}/cancel")
async def cancel(public_id: str, session: Session = Depends(get_session),
                 client: PayPalClient = Depends(paypal_client)):
    """Owner: cancel the order and release any hold."""
    order = get_order(session, public_id)
    return await run_step(session, order, service.cancel(session, client, order))


@router.post("/orders/{public_id}/refund")
async def refund(public_id: str, body: RefundIn, session: Session = Depends(get_session),
                 client: PayPalClient = Depends(paypal_client)):
    """Owner: give back part or all of one charge."""
    order = get_order(session, public_id)
    try:
        amount = to_cents(body.amount)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return await run_step(session, order, service.refund(session, client, order, body.capture_id, amount))
