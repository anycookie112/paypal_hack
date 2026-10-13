"""The setup pages' API: "View as" sign-in, reading the catalog and settings, and admins changing them."""

import pytest
from sqlmodel import Session

from app.db import engine
from tests.demo import item, make_group, member


@pytest.fixture
def people(client):
    """The demo group in the API's database; name -> X-Member-Id header."""
    with Session(engine) as session:
        make_group(session)
        return {name: {"X-Member-Id": str(member(session, name).id)} for name in ("Priya", "Dana", "Sam")}


def gloves_id():
    with Session(engine) as session:
        return item(session, "Gloves").id


def test_the_switcher_lists_everyone_admins_first(client, people):
    assert [(m["name"], m["role"]) for m in client.get("/api/members").json()] == [
        ("Sam", "admin"), ("Dana", "approver"), ("Marco", "member"), ("Priya", "member")]
    assert client.get("/api/me", headers=people["Priya"]).json()["name"] == "Priya"


@pytest.mark.parametrize("headers", [{}, {"X-Member-Id": "999"}])
def test_nobody_signed_in_is_turned_away(client, people, headers):
    response = client.get("/api/catalog", headers=headers)
    assert response.status_code == 401 and "View as" in response.json()["detail"]


def test_a_member_reads_the_catalog_but_cant_change_it(client, people):
    catalog = client.get("/api/catalog", headers=people["Priya"]).json()
    assert catalog["hard_ceiling_cents"] == 100_00
    assert catalog["categories"][0]["items"][0] == {
        "id": gloves_id(), "name": "Gloves", "aliases": ["work gloves", "disposable gloves", "cleanup gloves"],
        "cap_cents": 25_00, "restock_days": 14, "proof": "receipt_photo", "active": True}
    response = client.patch(f"/api/items/{gloves_id()}", json={"cap_cents": 30_00}, headers=people["Priya"])
    assert response.status_code == 403 and "Only the treasurer" in response.json()["detail"]


def test_the_admin_adds_and_switches_off_items(client, people):
    category = client.post("/api/categories", json={"name": "Tools", "monthly_budget_cents": 40_00},
                           headers=people["Sam"]).json()["categories"][-1]
    catalog = client.post("/api/items", json={"category_id": category["id"], "name": "Zip ties", "cap_cents": 8_00,
                                              "aliases": ["cable ties"]}, headers=people["Sam"]).json()
    assert [i["name"] for i in catalog["categories"][-1]["items"]] == ["Zip ties"]

    catalog = client.patch(f"/api/items/{gloves_id()}", json={"active": False}, headers=people["Sam"]).json()
    gloves = catalog["categories"][0]["items"][0]
    assert (gloves["active"], gloves["cap_cents"]) == (False, 25_00)  # only what was sent changes


def test_a_rule_broken_comes_back_in_words(client, people):
    response = client.patch(f"/api/items/{gloves_id()}", json={"cap_cents": 500_00}, headers=people["Sam"])
    assert response.status_code == 400 and "$100.00" in response.json()["detail"]


def test_settings_are_read_by_all_and_saved_by_the_admin(client, people):
    view = client.get("/api/settings", headers=people["Dana"]).json()
    assert view["name"] == "Riverside Volunteers" and view["settings"][0]["key"] == "hard_ceiling_cents"
    assert client.patch("/api/settings", json={"values": {"waiting_days": 5}}, headers=people["Dana"]).status_code == 403

    saved = client.patch("/api/settings", json={"monthly_budget_cents": 500_00, "values": {"waiting_days": 5}},
                         headers=people["Sam"]).json()
    assert saved["monthly_budget_cents"] == 500_00
    assert next(s for s in saved["settings"] if s["key"] == "waiting_days")["value"] == 5
