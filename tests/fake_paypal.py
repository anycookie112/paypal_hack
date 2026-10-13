"""A fake PayPal Payouts API for tests, behaving like the sandbox did in scripts/paypal/payouts.py."""

import json
from itertools import count

import httpx

from app.payments.paypal import PayPalClient


class FakePayPal:
    """`next_status` is what the next payout's item reports (SUCCESS, PENDING, UNCLAIMED, ...)."""

    def __init__(self):
        self.next_status = "SUCCESS"
        self.fee = "0.53"
        self.down = False            # every call fails with a 503
        self.items: dict[str, dict] = {}
        self.batches: dict[str, str] = {}      # batch id -> item id
        self.by_sender: dict[str, str] = {}    # sender_batch_id -> batch id
        self.created: list[dict] = []          # bodies of payouts PayPal accepted
        self._ids = count(1)

    def client(self) -> PayPalClient:
        return PayPalClient(client_id="id", client_secret="secret", base_url="https://api-m.sandbox.paypal.com",
                            transport=httpx.MockTransport(self.handle))

    def set_status(self, item_id: str, status: str) -> None:
        self.items[item_id]["transaction_status"] = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if self.down:
            return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE", "message": "Try again later."})
        if method == "POST" and path == "/v1/payments/payouts":
            return self._create(json.loads(request.content))
        if method == "GET" and path.startswith("/v1/payments/payouts/"):
            batch_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS"},
                                             "items": [self.items[self.batches[batch_id]]]})
        if method == "GET" and path.startswith("/v1/payments/payouts-item/"):
            return httpx.Response(200, json=self.items[path.rsplit("/", 1)[-1]])
        if method == "POST" and path.endswith("/cancel"):
            item = self.items[path.split("/")[-2]]
            if item["transaction_status"] != "UNCLAIMED":
                return httpx.Response(400, json={"name": "ITEM_INCORRECT_STATUS",
                                                 "message": "Only items in Unclaimed status can be cancelled."})
            item["transaction_status"] = "RETURNED"
            return httpx.Response(200, json=item)
        return httpx.Response(404, json={"name": "NOT_FOUND", "message": path})

    def _create(self, body: dict) -> httpx.Response:
        sender_batch_id = body["sender_batch_header"]["sender_batch_id"]
        if sender_batch_id in self.by_sender:
            href = f"https://api.sandbox.paypal.com/v1/payments/payouts/{self.by_sender[sender_batch_id]}"
            return httpx.Response(400, json={"name": "USER_BUSINESS_ERROR", "message": "User business error.",
                                             "details": [{"field": "SENDER_BATCH_ID", "location": "body",
                                                          "issue": "Batch with given sender_batch_id already exists",
                                                          "link": [{"href": href, "rel": "self", "method": "GET"}]}]})
        n = next(self._ids)
        batch_id, item_id = f"BATCH{n}", f"ITEM{n}"
        sent = body["items"][0]
        self.items[item_id] = {"payout_item_id": item_id, "payout_batch_id": batch_id,
                               "transaction_status": self.next_status,
                               "payout_item_fee": {"currency": "USD", "value": self.fee},
                               "payout_item": {"amount": sent["amount"], "receiver": sent["receiver"]}}
        self.batches[batch_id] = item_id
        self.by_sender[sender_batch_id] = batch_id
        self.created.append(body)
        return httpx.Response(201, json={"batch_header": {"payout_batch_id": batch_id, "batch_status": "PENDING"}})
