"""End-to-end check of app/core against the PayPal sandbox: request → claim → hold → payout (docs/SPEC.md §15).

    uv run python scripts/paypal/core_payout_check.py

Uses a throwaway database (not data/app.db). Priya is paid $18.40 at BUYER_EMAIL (a sandbox personal account).
The hold is skipped by running the payout job "3 days later".
"""

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(tempfile.mkdtemp()) / 'check.db').as_posix()}"
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select  # noqa: E402

from app.core.defaults import create_group  # noqa: E402
from app.core.payouts import balance_cents, record_top_up, run_due, start_hold  # noqa: E402
from app.core.requests import submit_request  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.domain.money import cents_to_str  # noqa: E402
from app.models import Claim, Item, Member, Notification, Payout, utcnow  # noqa: E402
from app.payments.paypal import PayPalClient  # noqa: E402


async def main() -> None:
    email = os.environ.get("BUYER_EMAIL")
    if not email:
        sys.exit("Set BUYER_EMAIL in .env (a sandbox personal account).")
    init_db()
    paypal = PayPalClient()
    with Session(engine) as session:
        group = create_group(session, "Riverside Volunteers")
        priya = Member(group_id=group.id, name="Priya", paypal_email=email)
        sam = Member(group_id=group.id, name="Sam", role="admin")
        session.add_all([priya, sam])
        session.commit()
        record_top_up(session, group, 100_00, note="sandbox check")

        gloves = session.exec(select(Item).where(Item.name == "Gloves")).one()
        now = utcnow()
        request = submit_request(session, priya, item_id=gloves.id, now=now).request
        print(f"1. Request: {request.status}, up to {cents_to_str(request.cap_cents)}")
        request.status = "claimed"
        claim = Claim(group_id=group.id, request_id=request.id, member_id=priya.id, item_id=gloves.id,
                      store="Riverside Hardware", receipt_date=now.date(), total_cents=18_40, submitted_at=now)
        session.add_all([request, claim])
        session.commit()
        start_hold(session, claim, now)
        print(f"2. Claim: {claim.status}, payout due {claim.payout_due_at:%a %d %b %H:%M}")

        early = await run_due(session, paypal, now + timedelta(days=1))
        print(f"3. Day 1: sent {early.sent} (nothing: still in the hold)")

        later = now + timedelta(days=3)
        result = await run_due(session, paypal, later)
        print(f"4. Day 3: sent {result.sent}, errors {result.errors}")
        for attempt in range(12):
            session.refresh(claim)
            if claim.status != "paying":
                break
            await asyncio.sleep(5)
            await run_due(session, paypal, later + timedelta(seconds=5 * (attempt + 1)))
        payout = session.exec(select(Payout)).one()
        print(f"5. PayPal: item {payout.paypal_item_id} {payout.status}, fee {cents_to_str(payout.fee_cents)}; "
              f"claim {claim.status}")
        print(f"   Balance: {cents_to_str(balance_cents(session, group))} (was $100.00)")
        for n in session.exec(select(Notification).where(Notification.member_id == priya.id)).all():
            print(f"   Priya was told: {n.text}")

        again = await run_due(session, paypal, later + timedelta(days=1))
        print(f"6. Running again: sent {again.sent} (never twice)")


if __name__ == "__main__":
    asyncio.run(main())
