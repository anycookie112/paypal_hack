"""PayPal sandbox spike: the money lifecycle of one service job.

Scenario (bedroom repaint, see SPEC.md):
  Quote:        materials $160 + labor up to $560  -> client approves ONE hold for $720
  Deposit:      capture $160 for materials right away (partial capture, hold stays open)
  Change order: "found water damage" -> client approves a SECOND hold for up to $150
  Finish:       labor actually $515 -> capture from hold #1, release the rest
                drywall actually $120 -> capture from hold #2, release the rest
  Refund:       materials came in $18.50 under -> partial refund of the deposit capture

Questions this answers (SPEC.md section 13):
  1. Multiple partial captures from one authorization (final_capture=false, then true)
  2. A second authorization for a change order on the same job
  3. final_capture=true releases the uncaptured remainder
  4. PayPal fee + net amount readable from each capture
  5. Partial refund of a capture

Usage:
  python scripts/paypal/holds_and_captures.py setup       # create quote + change-order orders, print approval links
  python scripts/paypal/holds_and_captures.py authorize   # after approving both links in the browser
  python scripts/paypal/holds_and_captures.py deposit     # capture materials deposit from hold #1
  python scripts/paypal/holds_and_captures.py finish      # capture actual labor + change order, release remainders
  python scripts/paypal/holds_and_captures.py refund      # refund part of the materials deposit
  python scripts/paypal/holds_and_captures.py status      # show every order, hold, capture, refund
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parents[1] / ".env")

BASE_URL = "https://api-m.sandbox.paypal.com"
STATE_FILE = ROOT / "state" / "holds_and_captures.json"
LOG_DIR = ROOT / "logs"
CUR = "USD"

CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")

QUOTE = {
    "key": "quote",
    "description": "Quote: bedroom repaint 12x14, walls + ceiling, dark blue to white",
    "items": [
        ("Materials: paint, primer, supplies", "160.00"),
        ("Labor and service (up to 8 hours)", "560.00"),
    ],
}
CHANGE_ORDER = {
    "key": "change",
    "description": "Change order: water-damaged drywall repair (up to 2 hours)",
    "items": [("Drywall repair (up to 2 hours)", "150.00")],
}

DEPOSIT = "160.00"
LABOR_ACTUAL = "515.00"
CHANGE_ACTUAL = "120.00"
REFUND = "18.50"


# --- HTTP helpers ---------------------------------------------------------

_token = None


def token():
    global _token
    if _token is None:
        r = requests.post(f"{BASE_URL}/v1/oauth2/token", auth=(CLIENT_ID, CLIENT_SECRET),
                          data={"grant_type": "client_credentials"}, timeout=30)
        r.raise_for_status()
        _token = r.json()["access_token"]
    return _token


def call(method, path, body=None, request_id=None, allow_fail=False):
    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if request_id:
        headers["PayPal-Request-Id"] = request_id
    r = requests.request(method, f"{BASE_URL}{path}", headers=headers,
                         data=json.dumps(body) if body is not None else None, timeout=30)
    data = r.json() if r.content else {}
    log(method, path, r.status_code, body, data)
    if r.status_code >= 400:
        print(f"\n!! {method} {path} -> HTTP {r.status_code}  debug_id={r.headers.get('paypal-debug-id')}")
        print(json.dumps({k: data.get(k) for k in ("name", "message", "details")}, indent=2))
        if allow_fail:
            return None
        sys.exit(1)
    return data


def log(method, path, status, req, resp):
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S%f")[:-3]
    name = f"{stamp}_{method}_{path.strip('/').replace('/', '_')}.json"
    (LOG_DIR / name).write_text(json.dumps(
        {"method": method, "path": path, "status": status, "request": req, "response": resp}, indent=2))


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def money(value):
    return {"currency_code": CUR, "value": value}


def fmt_breakdown(obj, key):
    b = obj.get(key) or {}
    if not b:
        return "(no breakdown returned)"
    parts = [f"{k}={v.get('value')}" for k, v in b.items() if isinstance(v, dict) and "value" in v]
    return ", ".join(parts)


# --- Steps ----------------------------------------------------------------

def create_order(job_id, spec):
    total = f"{sum(float(v) for _, v in spec['items']):.2f}"
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [{
            "reference_id": spec["key"],
            "custom_id": job_id,
            "invoice_id": f"{job_id}-{spec['key']}-{uuid.uuid4().hex[:6]}",
            "description": spec["description"][:127],
            "amount": {**money(total), "breakdown": {"item_total": money(total)}},
            "items": [{"name": n[:127], "quantity": "1", "unit_amount": money(v)} for n, v in spec["items"]],
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Demo Painting Co",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": "https://example.com/approved",
            "cancel_url": "https://example.com/cancelled",
        }}},
    }
    order = call("POST", "/v2/checkout/orders", body, request_id=f"create-{job_id}-{spec['key']}")
    link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
    return {"order_id": order["id"], "max": total, "approve_link": link}


def setup():
    job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
    state = {"job_id": job_id, "orders": {}}
    for spec in (QUOTE, CHANGE_ORDER):
        state["orders"][spec["key"]] = create_order(job_id, spec)
    save_state(state)
    print(f"Job {job_id}\n")
    for key, o in state["orders"].items():
        print(f"[{key}] order {o['order_id']}  hold up to ${o['max']}")
        print(f"   approve: {o['approve_link']}\n")
    print("Approve BOTH links as the Personal (buyer) sandbox account, then run: authorize")


def authorize():
    state = load_state()
    for key, o in state["orders"].items():
        if o.get("authorization_id"):
            print(f"[{key}] already authorized: {o['authorization_id']}")
            continue
        order = call("GET", f"/v2/checkout/orders/{o['order_id']}")
        pu = order["purchase_units"][0]
        if order["status"] != "APPROVED":
            print(f"[{key}] status {order['status']}: not approved yet")
            continue
        if pu.get("custom_id") != state["job_id"] or pu["amount"]["value"] != o["max"]:
            print(f"[{key}] REFUSING: PayPal order doesn't match job (custom_id/amount)")
            continue
        result = call("POST", f"/v2/checkout/orders/{o['order_id']}/authorize",
                      request_id=f"auth-{o['order_id']}")
        auth = result["purchase_units"][0]["payments"]["authorizations"][0]
        o["authorization_id"] = auth["id"]
        save_state(state)
        print(f"[{key}] hold {auth['id']}: {auth['status']} ${auth['amount']['value']}")
        print(f"        honor period ends / expires: {auth.get('expiration_time')}")


def capture(auth_id, amount, final, label):
    body = {"amount": money(amount), "final_capture": final, "note_to_payer": label[:255]}
    cap = call("POST", f"/v2/payments/authorizations/{auth_id}/capture", body,
               request_id=f"cap-{auth_id}-{amount}-{final}")
    print(f"  capture {cap['id']}: {cap['status']} ${cap['amount']['value']} (final={final})")
    print(f"     seller receives: {fmt_breakdown(cap, 'seller_receivable_breakdown')}")
    return cap["id"]


def deposit():
    state = load_state()
    q = state["orders"]["quote"]
    print(f"Materials deposit from hold {q['authorization_id']}:")
    q["deposit_capture_id"] = capture(q["authorization_id"], DEPOSIT, False, "Materials deposit")
    save_state(state)
    show_auth("quote", q["authorization_id"])


def finish():
    state = load_state()
    q, c = state["orders"]["quote"], state["orders"]["change"]
    print(f"Labor actual ${LABOR_ACTUAL} from hold {q['authorization_id']} (final):")
    q["labor_capture_id"] = capture(q["authorization_id"], LABOR_ACTUAL, True, "Labor, 7.4 hours actual")
    print(f"Change order actual ${CHANGE_ACTUAL} from hold {c['authorization_id']} (final):")
    c["capture_id"] = capture(c["authorization_id"], CHANGE_ACTUAL, True, "Drywall repair, actual")
    save_state(state)
    show_auth("quote", q["authorization_id"])
    show_auth("change", c["authorization_id"])


def refund():
    state = load_state()
    cap_id = state["orders"]["quote"]["deposit_capture_id"]
    body = {"amount": money(REFUND), "note_to_payer": "Materials came in under estimate"}
    r = call("POST", f"/v2/payments/captures/{cap_id}/refund", body, request_id=f"refund-{cap_id}-{REFUND}")
    state["orders"]["quote"]["refund_id"] = r["id"]
    save_state(state)
    print(f"Refund {r['id']}: {r['status']} ${r['amount']['value']} on capture {cap_id}")
    print(f"   breakdown: {fmt_breakdown(r, 'seller_payable_breakdown')}")


def show_auth(key, auth_id):
    a = call("GET", f"/v2/payments/authorizations/{auth_id}")
    print(f"  [{key}] hold {a['id']}: {a['status']}  authorized=${a['amount']['value']}")


def status():
    state = load_state()
    if not state:
        print("No job yet. Run: setup")
        return
    print(f"Job {state['job_id']}")
    for key, o in state["orders"].items():
        order = call("GET", f"/v2/checkout/orders/{o['order_id']}")
        print(f"\n[{key}] order {o['order_id']}: {order['status']}")
        payments = order["purchase_units"][0].get("payments", {})
        for a in payments.get("authorizations", []):
            print(f"  hold {a['id']}: {a['status']}  ${a['amount']['value']}")
        for c in payments.get("captures", []):
            print(f"  capture {c['id']}: {c['status']}  ${c['amount']['value']}")
        for r in payments.get("refunds", []):
            print(f"  refund {r['id']}: {r['status']}  ${r['amount']['value']}")


if __name__ == "__main__":
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("Missing PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env")
    steps = {"setup": setup, "authorize": authorize, "deposit": deposit,
             "finish": finish, "refund": refund, "status": status}
    steps.get(sys.argv[1] if len(sys.argv) > 1 else "status", status)()
