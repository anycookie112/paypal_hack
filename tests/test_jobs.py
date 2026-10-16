"""The server's payout loop starts only when it should: never in tests, and never without PayPal."""

import asyncio

from app import jobs
from app.payments.paypal import PayPalClient


def test_the_payout_loop_is_off_in_tests():
    assert jobs.PAYOUT_INTERVAL_SECONDS == 0
    assert jobs.start(PayPalClient(client_id="id", client_secret="secret")) is None


def test_the_payout_loop_needs_paypal(monkeypatch):
    monkeypatch.setattr(jobs, "PAYOUT_INTERVAL_SECONDS", 300)
    assert jobs.start(PayPalClient(client_id="", client_secret="")) is None

    async def started():
        task = jobs.start(PayPalClient(client_id="id", client_secret="secret"))
        task.cancel()
        return task
    assert asyncio.run(started()) is not None
