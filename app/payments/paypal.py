"""PayPal REST client: Orders v2 and Payments v2, the calls proven in scripts/paypal/.

Every call that moves money takes a `request_id` (sent as PayPal-Request-Id), so retrying it can
never charge twice: PayPal returns the original result for a repeated id.
"""

import time

import httpx

from app.config import PAYPAL_BASE_URL, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET


class PayPalError(Exception):
    def __init__(self, status: int, name: str, message: str, debug_id: str = "", details: list | None = None):
        self.status, self.name, self.debug_id, self.details = status, name, debug_id, details or []
        issue = "; ".join(d.get("issue", "") + (f" ({d['description']})" if d.get("description") else "")
                          for d in self.details)
        super().__init__(f"PayPal {name or status}: {message}" + (f" [{issue}]" if issue else ""))


def money(cents: int, currency: str = "USD") -> dict:
    return {"currency_code": currency, "value": f"{cents // 100}.{cents % 100:02d}"}


def cents(amount: dict | None) -> int:
    """PayPal's {"value": "12.34"} -> 1234."""
    if not amount:
        return 0
    whole, _, frac = amount["value"].partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


class PayPalClient:
    def __init__(self, *, client_id: str = PAYPAL_CLIENT_ID, client_secret: str = PAYPAL_CLIENT_SECRET,
                 base_url: str = PAYPAL_BASE_URL, transport: httpx.AsyncBaseTransport | None = None):
        """`transport` lets tests stand in for PayPal (httpx.MockTransport)."""
        self.client_id, self.client_secret, self.base_url = client_id, client_secret, base_url
        self._transport = transport
        self._token: str | None = None
        self._token_expires = 0.0

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _access_token(self, http: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        r = await http.post("/v1/oauth2/token", auth=(self.client_id, self.client_secret),
                            data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PayPalError(r.status_code, "AUTHENTICATION_FAILURE", "check PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET")
        data = r.json()
        self._token, self._token_expires = data["access_token"], time.time() + data.get("expires_in", 3000)
        return self._token

    async def call(self, method: str, path: str, body: dict | None = None, *, request_id: str | None = None) -> dict:
        if not self.configured():
            raise PayPalError(0, "NOT_CONFIGURED", "set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET on the server")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30, transport=self._transport) as http:
            headers = {"Authorization": f"Bearer {await self._access_token(http)}",
                       "Prefer": "return=representation"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            r = await http.request(method, path, json=body, headers=headers)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise PayPalError(r.status_code, data.get("name", ""), data.get("message", r.reason_phrase),
                              r.headers.get("paypal-debug-id", ""), data.get("details"))
        return data

    # --- Orders v2 ---------------------------------------------------------------------------

    async def create_order(self, body: dict, *, request_id: str) -> dict:
        return await self.call("POST", "/v2/checkout/orders", body, request_id=request_id)

    async def get_order(self, order_id: str) -> dict:
        return await self.call("GET", f"/v2/checkout/orders/{order_id}")

    async def capture_order(self, order_id: str, *, request_id: str) -> dict:
        """Charge an approved intent=CAPTURE order in full."""
        return await self.call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id=request_id)

    async def authorize_order(self, order_id: str, *, request_id: str) -> dict:
        """Place the hold for an approved intent=AUTHORIZE order."""
        return await self.call("POST", f"/v2/checkout/orders/{order_id}/authorize", {}, request_id=request_id)

    # --- Payments v2 -------------------------------------------------------------------------

    async def capture_authorization(self, authorization_id: str, amount_cents: int, *, final: bool,
                                    request_id: str, currency: str = "USD") -> dict:
        """Take part of a hold. `final=True` releases whatever is left of it."""
        return await self.call("POST", f"/v2/payments/authorizations/{authorization_id}/capture",
                               {"amount": money(amount_cents, currency), "final_capture": final},
                               request_id=request_id)

    async def void_authorization(self, authorization_id: str, *, request_id: str) -> dict:
        """Release the rest of a hold without charging it."""
        return await self.call("POST", f"/v2/payments/authorizations/{authorization_id}/void", None,
                               request_id=request_id)

    async def refund_capture(self, capture_id: str, amount_cents: int, *, request_id: str,
                             currency: str = "USD") -> dict:
        return await self.call("POST", f"/v2/payments/captures/{capture_id}/refund",
                               {"amount": money(amount_cents, currency)}, request_id=request_id)


def approval_url(order: dict) -> str:
    return next(link["href"] for link in order.get("links", []) if link["rel"] in ("payer-action", "approve"))


def capture_fee_cents(capture: dict) -> int:
    return cents((capture.get("seller_receivable_breakdown") or {}).get("paypal_fee"))
