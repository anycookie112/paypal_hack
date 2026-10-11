"""Payment plans and the PayPal payment steps, against a fake PayPal (no network)."""

import json
from datetime import date, timedelta

import httpx
import pytest

from app.api.orders import paypal_client
from app.main import app
from app.models import Product, Rule
from app.payments.paypal import PayPalClient, money
from app.payments.plan import PlanError, build_plan, final_amount_cents


# --- the plan ------------------------------------------------------------------------------------

def product(id, name, category, cents, pricing="fixed", unit="each"):
    return Product(id=id, business_id=1, name=name, category=category, price_cents=cents, pricing=pricing, unit=unit)


CUPCAKES = product(1, "Cupcakes (12)", "Cupcakes", 2000)
CAKE = product(2, "Custom cake", "Custom cakes", 4000)
RYE = product(3, "Rye bread", "Bread", 450, pricing="per_weight", unit="lb")
WEDDING = product(4, "Wedding cake", "Custom cakes", 0, pricing="quote")
RULES = [
    Rule(kind="payment", applies_to="Custom cakes", params_json='{"style": "deposit_balance", "deposit_percent": 50}'),
    Rule(kind="payment", applies_to="Bread", params_json='{"style": "hold_exact", "buffer_percent": 15}'),
]


def test_mixed_cart_holds_everything_and_charges_what_is_due_now():
    plan = build_plan([(CUPCAKES, 1), (CAKE, 1), (RYE, 2.5)], RULES)
    assert plan.intent == "AUTHORIZE"
    assert [line.style for line in plan.lines] == ["pay_now", "deposit_balance", "hold_exact"]
    assert plan.estimate_cents == 2000 + 4000 + 1125
    assert plan.hold_cents == 2000 + 4000 + 1125 + 169   # 15% of $11.25 rounded up
    assert plan.due_now_cents == 2000 + 2000              # cupcakes in full + 50% deposit
    assert plan.due_later_cents == 2000 + 1125            # cake balance + bread estimate
    assert "$40.00 is charged when you approve." in plan.summary()


def test_all_pay_now_is_a_plain_charge():
    plan = build_plan([(CUPCAKES, 2)], RULES)
    assert plan.intent == "CAPTURE" and plan.summary() == ["You pay $40.00 now."]


@pytest.mark.parametrize("items,message", [
    ([(WEDDING, 1)], "priced on request"),
    ([(CUPCAKES, 1.5)], "whole number"),
    ([(CUPCAKES, 0)], "more than 0"),
    ([], "empty"),
])
def test_invalid_carts(items, message):
    with pytest.raises(PlanError, match=message):
        build_plan(items, RULES)


def test_holds_cannot_outlive_paypal_authorizations():
    far = date.today() + timedelta(days=40)
    with pytest.raises(PlanError, match="29 days"):
        build_plan([(CAKE, 1)], RULES, ready_by=far)
    assert build_plan([(CUPCAKES, 1)], RULES, ready_by=far).intent == "CAPTURE"  # paid in full now: fine


def test_final_amount_uses_weighed_prices_and_never_exceeds_the_hold():
    plan = build_plan([(CUPCAKES, 1), (CAKE, 1), (RYE, 2.5)], RULES)
    assert final_amount_cents(plan, {2: 1250}) == 2000 + 1250
    plan = build_plan([(CUPCAKES, 1), (CAKE, 1), (RYE, 2.5)], RULES)
    with pytest.raises(PlanError, match="more than the"):
        final_amount_cents(plan, {2: 1300})  # $13.00 > $11.25 + $1.69 buffer


# --- a fake PayPal ------------------------------------------------------------------------------

class FakePayPal:
    """Answers like PayPal's sandbox for the calls we make, and replays repeated PayPal-Request-Ids."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.orders: dict[str, dict] = {}
        self.replayed: dict[str, httpx.Response] = {}
        self.fail_next: tuple[int, str] | None = None
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    @staticmethod
    def _fee(cents):
        return money(round(cents * 0.0349) + 49)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        body = json.loads(request.content) if request.content else None
        rid = request.headers.get("PayPal-Request-Id")
        if rid in self.replayed:
            return self.replayed[rid]
        self.calls.append((request.method, path, body))
        if self.fail_next:
            status, name = self.fail_next
            self.fail_next = None
            return httpx.Response(status, json={"name": name, "message": "Fake failure"},
                                  headers={"paypal-debug-id": "dbg123"})
        response = httpx.Response(200, json=self.route(request.method, path, body))
        if rid:
            self.replayed[rid] = response
        return response

    def route(self, method, path, body):
        parts = path.strip("/").split("/")
        payer = {"name": {"given_name": "Ada", "surname": "Lovelace"}, "email_address": "ada@example.com"}
        if path == "/v2/checkout/orders":
            order_id = self._id("ORDER")
            self.orders[order_id] = body
            return {"id": order_id, "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": f"https://paypal.test/approve/{order_id}"}]}
        if parts[:3] == ["v2", "checkout", "orders"] and parts[-1] in ("capture", "authorize"):
            amount = self.orders[parts[3]]["purchase_units"][0]["amount"]
            cents = int(amount["value"].replace(".", ""))
            if parts[-1] == "capture":
                payment = {"captures": [{"id": self._id("CAP"), "status": "COMPLETED", "amount": amount,
                                         "seller_receivable_breakdown": {"paypal_fee": self._fee(cents)}}]}
            else:
                payment = {"authorizations": [{"id": self._id("AUTH"), "status": "CREATED", "amount": amount}]}
            return {"id": parts[3], "status": "COMPLETED", "payer": payer,
                    "purchase_units": [{"payments": payment}]}
        if parts[:3] == ["v2", "payments", "authorizations"] and parts[-1] == "capture":
            cents = int(body["amount"]["value"].replace(".", ""))
            return {"id": self._id("CAP"), "status": "COMPLETED", "amount": body["amount"],
                    "final_capture": body["final_capture"],
                    "seller_receivable_breakdown": {"paypal_fee": self._fee(cents)}}
        if parts[-1] == "void":
            return {"id": parts[3], "status": "VOIDED"}
        if parts[-1] == "refund":
            return {"id": self._id("REF"), "status": "COMPLETED", "amount": body["amount"]}
        raise AssertionError(f"Unexpected PayPal call {method} {path}")


@pytest.fixture
def paypal(client):
    fake = FakePayPal()
    pp = PayPalClient(client_id="id", client_secret="secret", transport=httpx.MockTransport(fake.handler))
    app.dependency_overrides[paypal_client] = lambda: pp
    yield fake
    app.dependency_overrides.pop(paypal_client, None)


def add_product(client, slug, **fields):
    row = {"name": "", "category": "", "price": "", "pricing": "fixed", "unit": "each", "tags": "",
           "lead_time_hours": "0", "description": "", **fields}
    r = client.post(f"/api/businesses/{slug}/products", json=row)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def shop(client, bakery):
    ids = {
        "cupcakes": add_product(client, bakery, name="Cupcakes (12)", category="Cupcakes", price="20"),
        "cake": add_product(client, bakery, name="Custom cake", category="Custom cakes", price="40"),
        "rye": add_product(client, bakery, name="Rye bread", category="Bread", price="4.50", pricing="per_weight", unit="lb"),
    }
    client.post(f"/api/businesses/{bakery}/rules", json={"kind": "payment", "applies_to": "Custom cakes",
                "params": {"style": "deposit_balance", "deposit_percent": 50}})
    client.post(f"/api/businesses/{bakery}/rules", json={"kind": "payment", "applies_to": "Bread",
                "params": {"style": "hold_exact", "buffer_percent": 15}})
    return bakery, ids


def place(client, slug, lines):
    r = client.post(f"/api/businesses/{slug}/orders", json={"lines": lines})
    assert r.status_code == 201, r.text
    return r.json()["public_id"]


def back_from_paypal(client, paypal):
    order_id = next(reversed(paypal.orders))
    return client.get(f"/api/paypal/return?token={order_id}&PayerID=X", follow_redirects=False)


# --- the steps, through the API ------------------------------------------------------------------

def test_hold_deposit_then_exact_final_charge(client, paypal, shop):
    slug, ids = shop
    oid = place(client, slug, [{"product_id": ids["cupcakes"], "quantity": 1},
                               {"product_id": ids["cake"], "quantity": 1},
                               {"product_id": ids["rye"], "quantity": 2.5}])

    approve = client.post(f"/api/orders/{oid}/checkout").json()["approval_url"]
    assert approve.startswith("https://paypal.test/approve/")
    sent = next(iter(paypal.orders.values()))
    unit = sent["purchase_units"][0]
    assert sent["intent"] == "AUTHORIZE" and unit["payee"] == {"email_address": "bakery@example.com"}
    assert unit["amount"]["value"] == "72.94"
    assert sum(int(i["unit_amount"]["value"].replace(".", "")) for i in unit["items"]) == 7294
    assert unit["items"][-1]["name"].startswith("Extra hold")

    r = back_from_paypal(client, paypal)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/b/{slug}/orders/{oid}")
    order = client.get(f"/api/orders/{oid}").json()
    assert order["status"] == "held" and order["captured_cents"] == 4000
    assert order["customer_name"] == "Ada Lovelace"
    assert [(p["action"], p["amount_cents"], p["final"]) for p in order["payments"]] == \
        [("create", 7294, False), ("authorize", 7294, False), ("capture", 4000, False)]

    back_from_paypal(client, paypal)  # reloading the return page changes nothing
    assert len(client.get(f"/api/orders/{oid}").json()["payments"]) == 3

    order = client.post(f"/api/orders/{oid}/complete", json={"final_amounts": {"2": "12.50"}}).json()
    assert order["status"] == "completed" and order["captured_cents"] == 4000 + 2000 + 1250
    assert order["payments"][-1]["final"] is True and order["payments"][-1]["amount_cents"] == 3250
    assert order["plan"]["lines"][2]["final_cents"] == 1250
    assert order["fee_cents"] > 0


def test_pay_now_cart_is_charged_on_return(client, paypal, shop):
    slug, ids = shop
    oid = place(client, slug, [{"product_id": ids["cupcakes"], "quantity": 2}])
    client.post(f"/api/orders/{oid}/checkout")
    assert next(iter(paypal.orders.values()))["intent"] == "CAPTURE"
    back_from_paypal(client, paypal)
    order = client.get(f"/api/orders/{oid}").json()
    assert order["status"] == "paid" and order["captured_cents"] == 4000


def test_cancel_releases_the_hold_and_refund_is_limited_to_the_charge(client, paypal, shop):
    slug, ids = shop
    oid = place(client, slug, [{"product_id": ids["cake"], "quantity": 1}])
    client.post(f"/api/orders/{oid}/checkout")
    back_from_paypal(client, paypal)

    order = client.post(f"/api/orders/{oid}/cancel").json()
    assert order["status"] == "cancelled" and order["payments"][-1]["action"] == "void"

    deposit = next(p for p in order["payments"] if p["action"] == "capture")
    r = client.post(f"/api/orders/{oid}/refund", json={"capture_id": deposit["paypal_id"], "amount": "25"})
    assert r.status_code == 409 and "$20.00" in r.json()["detail"]
    order = client.post(f"/api/orders/{oid}/refund", json={"capture_id": deposit["paypal_id"], "amount": "20"}).json()
    assert order["refunded_cents"] == 2000 and order["payments"][-1]["parent_id"] == deposit["paypal_id"]
    assert client.post(f"/api/orders/{oid}/complete", json={}).status_code == 409


def test_cart_validation(client, paypal, shop, bakery):
    slug, ids = shop
    r = client.post(f"/api/businesses/{slug}/orders", json={"lines": [{"product_id": 999, "quantity": 1}]})
    assert r.status_code == 422 and "999" in r.json()["detail"]
    other = client.post("/api/businesses", json={"name": "Other", "paypal_email": "o@example.com"}).json()["slug"]
    r = client.post(f"/api/businesses/{other}/orders", json={"lines": [{"product_id": ids["cake"], "quantity": 1}]})
    assert r.status_code == 422  # another shop's product


def test_paypal_errors_are_reported_and_nothing_is_recorded(client, paypal, shop):
    slug, ids = shop
    oid = place(client, slug, [{"product_id": ids["cake"], "quantity": 1}])
    client.post(f"/api/orders/{oid}/checkout")
    back_from_paypal(client, paypal)
    paypal.fail_next = (422, "AUTHORIZATION_VOIDED")
    r = client.post(f"/api/orders/{oid}/complete", json={})
    assert r.status_code == 502 and "AUTHORIZATION_VOIDED" in r.json()["detail"] and "dbg123" in r.json()["detail"]
    order = client.get(f"/api/orders/{oid}").json()
    assert order["status"] == "held" and len(order["payments"]) == 3
