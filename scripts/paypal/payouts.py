"""PayPal sandbox spike: reimbursing employees with Payouts (the office fund, see SPEC.md section 7).

Scenario: Sam bought coffee for the office ($24.50); after the waiting period the company pays Sam back.

Questions this answers:
  1. A payout to an existing PayPal account: how its status moves (PENDING -> SUCCESS) and how fast
  2. The fee on a payout, and who pays it (sender or recipient)
  3. A payout to an email with no PayPal account: UNCLAIMED, and can we cancel it (money back to us)?
  4. Can a payout that already succeeded be cancelled? (expected: no, so the "hold" must live in our app)
  5. Retrying with the same sender_batch_id: rejected, so a retry can never pay twice?
  6. The sender's balance before and after (if the app has the reporting scope)

Usage:
  python scripts/paypal/payouts.py balance      # the company account's sandbox balance
  python scripts/paypal/payouts.py send         # Q1, Q2: pay $24.50 to BUYER_EMAIL, wait for the result
  python scripts/paypal/payouts.py unclaimed    # Q3: pay $5.00 to an email with no PayPal account
  python scripts/paypal/payouts.py cancel       # Q3, Q4: cancel the unclaimed payout, then try the successful one
  python scripts/paypal/payouts.py duplicate    # Q5: resend the first batch with the same sender_batch_id
  python scripts/paypal/payouts.py status       # every batch and item so far
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holds_and_captures as pp  # reuse token/call/log helpers

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
EMPLOYEE_EMAIL = os.environ.get("BUYER_EMAIL")  # a sandbox personal account: stands in for Sam
STATE_FILE = Path(__file__).resolve().parent / "state" / "payouts.json"


def load():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def amount(value):
    return {"value": value, "currency": pp.CUR}  # Payouts (v1) says "currency", not "currency_code"


def batch_body(sender_batch_id, receiver, value, note, item_id):
    return {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "You've been reimbursed by the office fund",
            "email_message": "Thanks for restocking the office.",
        },
        "items": [{
            "recipient_type": "EMAIL",
            "receiver": receiver,
            "amount": amount(value),
            "note": note,
            "sender_item_id": item_id,
        }],
    }


def create_payout(key, receiver, value, note):
    """Send a one-item payout batch and wait until PayPal has finished processing it."""
    sender_batch_id = f"{key}-{uuid.uuid4().hex[:10]}"
    body = batch_body(sender_batch_id, receiver, value, note, f"claim-{key}")
    started = time.time()
    created = pp.call("POST", "/v1/payments/payouts", body)
    batch_id = created["batch_header"]["payout_batch_id"]
    print(f"{key}: batch {batch_id} created, status {created['batch_header']['batch_status']}")

    batch = wait_for(batch_id)
    item = batch["items"][0]
    elapsed = time.time() - started
    print(f"{key}: batch {batch['batch_header']['batch_status']}, item {item['transaction_status']} "
          f"after {elapsed:.0f}s")
    print(f"  amount {item['payout_item']['amount']['value']}, item fee "
          f"{(item.get('payout_item_fee') or {}).get('value', '?')}, batch fees "
          f"{(batch['batch_header'].get('fees') or {}).get('value', '?')}")

    state = load()
    state[key] = {"sender_batch_id": sender_batch_id, "body": body, "batch_id": batch_id,
                  "item_id": item["payout_item_id"], "status": item["transaction_status"],
                  "seconds": round(elapsed)}
    save(state)


def wait_for(batch_id, timeout=120):
    """Poll the batch until it leaves PENDING/PROCESSING (or the timeout passes)."""
    deadline = time.time() + timeout
    while True:
        batch = pp.call("GET", f"/v1/payments/payouts/{batch_id}")
        status = batch["batch_header"]["batch_status"]
        if status not in ("PENDING", "PROCESSING") or time.time() > deadline:
            return batch
        print(f"  ... {status}")
        time.sleep(5)


def balance():
    data = pp.call("GET", "/v1/reporting/balances", allow_fail=True)  # no query string: the log helper makes it a filename
    if data is None:
        print("No balance: the app may not have the reporting scope (not needed for payouts).")
        return
    for b in data.get("balances", []):
        print(f"{b['currency']}: available {b['available_balance']['value']}, total {b['total_balance']['value']}")


def send():
    if not EMPLOYEE_EMAIL:
        sys.exit("Set BUYER_EMAIL in .env (a sandbox personal account) to receive the payout.")
    create_payout("registered", EMPLOYEE_EMAIL, "24.50", "Coffee for the office (2 bags), receipt #1042")


def unclaimed():
    nobody = f"no-paypal-{uuid.uuid4().hex[:8]}@example.com"
    create_payout("unclaimed", nobody, "5.00", "Test: recipient with no PayPal account")


def cancel():
    state = load()
    for key in ("unclaimed", "registered"):
        if key not in state:
            print(f"{key}: run '{'send' if key == 'registered' else key}' first")
            continue
        item_id = state[key]["item_id"]
        result = pp.call("POST", f"/v1/payments/payouts-item/{item_id}/cancel", allow_fail=True)
        if result is None:
            print(f"{key}: cancel refused (see the error above)")
        else:
            print(f"{key}: cancelled, item now {result.get('transaction_status')}")
            state[key]["status"] = result.get("transaction_status")
    save(state)


def duplicate():
    state = load()
    if "registered" not in state:
        sys.exit("Run 'send' first.")
    print(f"Resending sender_batch_id {state['registered']['sender_batch_id']} ...")
    result = pp.call("POST", "/v1/payments/payouts", state["registered"]["body"], allow_fail=True)
    if result is None:
        print("Rejected: the same sender_batch_id can't pay twice.")
    else:
        print(f"!! Accepted again: batch {result['batch_header']['payout_batch_id']}, "
              f"status {result['batch_header']['batch_status']}")


def status():
    for key, saved in load().items():
        batch = pp.call("GET", f"/v1/payments/payouts/{saved['batch_id']}")
        for item in batch["items"]:
            print(f"{key}: batch {saved['batch_id']} {batch['batch_header']['batch_status']}, "
                  f"item {item['payout_item_id']} {item['transaction_status']}, "
                  f"{item['payout_item']['amount']['value']} to {item['payout_item']['receiver']}")


COMMANDS = {"balance": balance, "send": send, "unclaimed": unclaimed, "cancel": cancel,
            "duplicate": duplicate, "status": status}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
