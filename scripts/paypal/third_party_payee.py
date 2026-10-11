"""PayPal sandbox spike: can a platform hold + capture money on behalf of another business?

Platform model: our app's credentials create the order, but the money must land in the
business's own PayPal account (purchase_units[].payee). Question: do AUTHORIZE, partial
capture and final capture still work when the payee is a different account?

Uses SUPPLIER_EMAIL from .env (a separate sandbox BUSINESS account) as the business ("Demo Bakery") receiving the money.

Usage:
  python scripts/paypal/third_party_payee.py create      # order: hold $60 for Demo Bakery, prints approval link
  python scripts/paypal/third_party_payee.py run         # after approving: authorize, capture deposit, capture balance
  python scripts/paypal/third_party_payee.py status
"""

import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holds_and_captures as pp  # reuse token/call/log helpers

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
PAYEE_EMAIL = os.environ.get("SUPPLIER_EMAIL")
STATE_FILE = Path(__file__).resolve().parent / "state" / "third_party_payee.json"


def load():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def create():
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [{
            "reference_id": "bakery-order",
            "description": "Demo Bakery: 6-inch custom cake + 24 cupcakes",
            "payee": {"email_address": PAYEE_EMAIL},
            "amount": {**pp.money("60.00"), "breakdown": {"item_total": pp.money("60.00")}},
            "items": [
                {"name": "6-inch custom cake", "quantity": "1", "unit_amount": pp.money("36.00")},
                {"name": "Vanilla cupcakes (24)", "quantity": "1", "unit_amount": pp.money("24.00")},
            ],
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Demo Bakery",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": "https://example.com/approved",
            "cancel_url": "https://example.com/cancelled",
        }}},
    }
    order = pp.call("POST", "/v2/checkout/orders", body, request_id=f"payee-create-{uuid.uuid4()}")
    link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
    save({"order_id": order["id"]})
    print(f"Order {order['id']} ({order['status']}), payee requested: {PAYEE_EMAIL}")
    print(f"\nApprove as the Personal sandbox buyer:\n  {link}\n\nThen run: python scripts/paypal/third_party_payee.py run")


def run():
    state = load()
    order = pp.call("GET", f"/v2/checkout/orders/{state['order_id']}")
    print(f"Order status: {order['status']}")
    if order["status"] != "APPROVED":
        print("Not approved yet.")
        return
    print(f"Payee on order: {json.dumps(order['purchase_units'][0].get('payee'))}")

    result = pp.call("POST", f"/v2/checkout/orders/{state['order_id']}/authorize",
                     request_id=f"payee-auth-{state['order_id']}", allow_fail=True)
    if result is None:
        print("\nRESULT: authorize FAILED with a third-party payee (see error above).")
        return
    auth = result["purchase_units"][0]["payments"]["authorizations"][0]
    state["authorization_id"] = auth["id"]
    save(state)
    print(f"Hold {auth['id']}: {auth['status']} ${auth['amount']['value']}")

    for label, amount, final in (("deposit (50% of cake)", "18.00", False), ("balance at pickup", "42.00", True)):
        cap = pp.call("POST", f"/v2/payments/authorizations/{auth['id']}/capture",
                      {"amount": pp.money(amount), "final_capture": final},
                      request_id=f"payee-cap-{auth['id']}-{amount}", allow_fail=True)
        if cap is None:
            print(f"\nRESULT: capture of {label} FAILED with a third-party payee.")
            return
        print(f"Capture {label}: {cap['status']} ${cap['amount']['value']}  "
              f"seller: {pp.fmt_breakdown(cap, 'seller_receivable_breakdown')}")
        detail = pp.call("GET", f"/v2/payments/captures/{cap['id']}")
        print(f"   received by payee: {json.dumps(detail.get('payee'))}")
    print("\nRESULT: hold + deposit + balance all work with a third-party payee.")


def status():
    state = load()
    order = pp.call("GET", f"/v2/checkout/orders/{state['order_id']}")
    pu = order["purchase_units"][0]
    print(f"Order {order['id']}: {order['status']}  payee={json.dumps(pu.get('payee'))}")
    for kind in ("authorizations", "captures"):
        for p in pu.get("payments", {}).get(kind, []):
            print(f"  {kind[:-1]} {p['id']}: {p['status']} ${p['amount']['value']}")


if __name__ == "__main__":
    if not PAYEE_EMAIL:
        sys.exit("Missing SUPPLIER_EMAIL in .env (a separate sandbox BUSINESS account, not the buyer)")
    {"create": create, "run": run, "status": status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
