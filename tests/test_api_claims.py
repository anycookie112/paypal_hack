"""The claims API: what a member can claim, handing in a receipt, and PICs deciding on flagged ones."""

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import Member
from tests.demo import item, make_group, member

JPG = b"\xff\xd8\xff\xe0 a receipt"


@pytest.fixture
def people(client):
    """The demo group in the API's database, everyone with a PayPal email; name -> X-Member-Id header."""
    with Session(engine) as session:
        make_group(session)
        for m in session.exec(select(Member)).all():
            m.paypal_email = f"{m.name.lower()}@paypal.example.com"
            session.add(m)
        session.commit()
        return {name: {"X-Member-Id": str(member(session, name).id)} for name in ("Priya", "Marco", "Dana")}


def get(client, who, what):
    """`who` tells the group they're getting `what` (through the core, as the chat would)."""
    from app.core.requests import submit_request
    with Session(engine) as session:
        return submit_request(session, member(session, who), item_id=item(session, what).id).request.id


def hand_in(client, headers, request_id, *, receipt=JPG, total="11.99", **form):
    today = datetime.now(timezone.utc).date().isoformat()  # the app's dates are UTC
    data = {"request_id": str(request_id), "store": "Riverside Hardware", "receipt_date": today, "total": total, **form}
    files = {"receipt": ("receipt.jpg", receipt, "image/jpeg")} if receipt else {}
    return client.post("/api/claims", data=data, files=files, headers=headers)


def test_a_member_claims_their_request(client, people):
    request_id = get(client, "Priya", "Trash bags")
    [claimable] = client.get("/api/claimable", headers=people["Priya"]).json()
    assert (claimable["request_id"], claimable["item"], claimable["needs_photo"]) == (request_id, "Trash bags", False)

    response = hand_in(client, people["Priya"], request_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["outcome"], body["claim"]["status"], body["claim"]["total_cents"]) == ("accepted", "waiting", 11_99)
    assert client.get("/api/claimable", headers=people["Priya"]).json() == []

    receipt = body["claim"]["receipt_url"]
    assert client.get(receipt, headers=people["Priya"]).content == JPG   # her own receipt
    assert client.get(receipt, headers=people["Dana"]).status_code == 200  # a PIC
    assert client.get(receipt, headers=people["Marco"]).status_code == 404  # nobody else


def test_whats_missing_comes_back_as_a_list(client, people):
    request_id = get(client, "Priya", "Gloves")  # gloves need a photo of where they're kept
    response = hand_in(client, people["Priya"], request_id, receipt=None, total="")
    assert response.status_code == 400
    assert response.json()["missing"] == ["the receipt", "the total", "a photo of where it's kept"]


def test_a_bad_total_is_explained(client, people):
    response = hand_in(client, people["Priya"], get(client, "Priya", "Trash bags"), total="eleven")
    assert response.status_code == 400 and response.json()["detail"].startswith("The total:")


def test_a_pic_decides_on_a_flagged_receipt(client, people):
    flagged = hand_in(client, people["Priya"], get(client, "Priya", "Trash bags"),
                      receipt_date="2026-10-01").json()  # dated before it was confirmed
    assert flagged["outcome"] == "review" and "before it was confirmed" in flagged["claim"]["reasons"][0]

    assert client.get("/api/claims/review", headers=people["Marco"]).json() == []  # not a PIC
    [waiting] = client.get("/api/claims/review", headers=people["Dana"]).json()
    assert waiting["id"] == flagged["claim"]["id"]
    assert client.post(f"/api/claims/{waiting['id']}/accept", headers=people["Marco"]).status_code == 400
    accepted = client.post(f"/api/claims/{waiting['id']}/accept", headers=people["Dana"]).json()
    assert accepted["status"] == "waiting"


def fake_reader(monkeypatch, total="11.99", fails=False):
    """The receipt reader without a model: it 'reads' this total off any receipt, and remembers what it was asked."""
    from app.ai.model import AgentError
    from app.core.receipts import Reading
    asked = []

    async def read(data, kind, *, what):
        asked.append((kind, what))
        if fails:
            raise AgentError("receipt: couldn't read it (TimeoutError)")
        today = datetime.now(timezone.utc).date().isoformat()
        return Reading(readable=True, store="Riverside Hardware", date=today, date_as_printed=None, currency="USD",
                       total=total, lines=[], has_the_item="yes", unsure=[]), {"prompt_id": "receipt@v1", "ms": 5,
                                                                              "input_tokens": 1, "output_tokens": 1}
    monkeypatch.setattr("app.api.claims.read_receipt", read)
    return asked


def read(client, headers, request_id, receipt=JPG):
    return client.post("/api/claims/read", data={"request_id": str(request_id)},
                       files={"receipt": ("receipt.jpg", receipt, "image/jpeg")}, headers=headers)


def test_the_reader_fills_in_the_form_and_a_different_total_goes_to_a_pic(client, people, monkeypatch):
    asked = fake_reader(monkeypatch, total="9.99")
    request_id = get(client, "Priya", "Trash bags")
    body = read(client, people["Priya"], request_id).json()
    assert (body["reading"]["store"], body["reading"]["total"], body["currency"]) == ("Riverside Hardware", "9.99",
                                                                                      "USD")
    assert asked == [(".jpg", "Trash bags")]
    assert read(client, people["Marco"], request_id).status_code == 400  # not his request

    handed = hand_in(client, people["Priya"], request_id, total="11.99").json()  # she typed another total
    assert (handed["outcome"], handed["claim"]["reasons"]) == (
        "review", ["The receipt reader read a total of $9.99; Priya entered $11.99."])


def test_without_a_reading_the_member_types_the_values(client, people, monkeypatch):
    fake_reader(monkeypatch, fails=True)
    request_id = get(client, "Priya", "Trash bags")
    assert read(client, people["Priya"], request_id).json() == {
        "reading": None, "note": "Couldn't read it just now: fill in the store, date and total.", "currency": "USD"}
    assert hand_in(client, people["Priya"], request_id).json()["outcome"] == "accepted"  # checked as before
