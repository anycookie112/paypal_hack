"""Work the server does on its own: paying back claims whose hold is over (docs/SPEC.md §14, rule 6).

Every PAYOUT_INTERVAL_SECONDS (default 5 minutes) it runs payouts.run_due, which is safe to run as often as you like:
nothing is ever paid twice (each payout's sender_batch_id comes from its claim). It only runs when PayPal is
configured, and never in tests (PAYOUT_INTERVAL_SECONDS=0).
"""

import asyncio
import logging

from sqlmodel import Session

from app.config import PAYOUT_INTERVAL_SECONDS
from app.core.payouts import run_due
from app.db import engine
from app.payments.paypal import PayPalClient

log = logging.getLogger(__name__)


async def pay_due_once(paypal: PayPalClient) -> None:
    with Session(engine) as session:
        report = await run_due(session, paypal)
    if report.sent or report.updated or report.waiting or report.errors:
        log.info("Payouts: sent %s, updated %s, waiting %s, errors %s", report.sent, report.updated, report.waiting,
                 report.errors)


async def pay_due_forever(interval: float = PAYOUT_INTERVAL_SECONDS) -> None:
    paypal = PayPalClient()
    while True:
        try:
            await pay_due_once(paypal)
        except Exception:  # keep going: the next run retries, and nothing is paid twice
            log.exception("Paying what's due failed")
        await asyncio.sleep(interval)


def start(paypal: PayPalClient | None = None) -> asyncio.Task | None:
    """Start the loop, if PayPal is configured and it isn't switched off."""
    if PAYOUT_INTERVAL_SECONDS <= 0 or not (paypal or PayPalClient()).configured():
        return None
    return asyncio.create_task(pay_due_forever())
