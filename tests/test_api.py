from app.ai import onboarding
from app.domain.catalog import CSV_TEMPLATE

ROW = {"name": "", "category": "", "price": "", "pricing": "fixed", "unit": "each",
       "tags": "", "lead_time_hours": "0", "description": ""}


def row(**fields):
    return {**ROW, **fields}


def test_starts_with_no_data(client):
    assert client.get("/api/businesses").json() == []


def test_meta_lists_options(client):
    meta = client.get("/api/meta").json()
    assert set(meta["payment_styles"]) == {"pay_now", "hold_exact", "deposit_balance"}
    assert meta["ai_available"] is True


def test_business_requires_name_and_paypal_email(client):
    r = client.post("/api/businesses", json={"name": " ", "paypal_email": ""})
    assert r.status_code == 422


def test_slug_is_unique(client, bakery):
    r = client.post("/api/businesses", json={"name": "Sweet Crumb Bakery", "paypal_email": "b@example.com"})
    assert r.json()["slug"] == "sweet-crumb-bakery-2"


def test_unknown_business_is_404(client):
    assert client.get("/api/businesses/nope").status_code == 404


def test_csv_import_returns_drafts_without_saving(client, bakery):
    r = client.post(f"/api/businesses/{bakery}/products/import/csv",
                    files={"file": ("products.csv", CSV_TEMPLATE.encode(), "text/csv")})
    drafts = r.json()["drafts"]
    assert [d["fields"]["name"] for d in drafts][:2] == ["Sourdough loaf", "Vanilla cupcake"]
    assert all(not d["errors"] for d in drafts)
    assert client.get(f"/api/businesses/{bakery}").json()["products"] == []


def test_bad_csv_is_rejected(client, bakery):
    r = client.post(f"/api/businesses/{bakery}/products/import/csv",
                    files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 422 and "header row" in r.json()["detail"]


def test_bulk_save_and_detail(client, bakery):
    r = client.post(f"/api/businesses/{bakery}/products/bulk", json={"products": [
        row(name="Sourdough loaf", category="Bread", price="6.50", tags="vegan"),
        row(name="Bananas", category="Produce", price="0.59", pricing="per_weight", unit="lb"),
    ]})
    assert r.json() == {"saved": 2}
    detail = client.get(f"/api/businesses/{bakery}").json()
    assert {p["name"]: (p["price_cents"], p["pricing"]) for p in detail["products"]} == \
        {"Sourdough loaf": (650, "fixed"), "Bananas": (59, "per_weight")}
    assert detail["categories"] == ["Bread", "Produce"]


def test_invalid_rows_block_bulk_save(client, bakery):
    r = client.post(f"/api/businesses/{bakery}/products/bulk", json={"products": [
        row(name="Cake", price="abc"), row(name="Bread", price="4")]})
    assert r.status_code == 422
    assert "is not a price" in r.json()["drafts"][0]["errors"][0]
    assert client.get(f"/api/businesses/{bakery}").json()["products"] == []


def test_add_and_delete_product(client, bakery):
    p = client.post(f"/api/businesses/{bakery}/products", json=row(name="Croissant", price="3.25")).json()
    assert p["price_cents"] == 325
    assert client.delete(f"/api/businesses/{bakery}/products/{p['id']}").status_code == 204
    assert client.get(f"/api/businesses/{bakery}").json()["products"] == []


def test_ai_products_go_through_validation(client, bakery, monkeypatch):
    async def fake_extract(kind, text):
        return [{"name": "Croissant", "price": 3.25, "pricing": "fixed", "tags": ["vegetarian"]},
                {"name": "Wedding cake", "price": None, "pricing": "quote"},
                {"name": "", "price": 2}]
    monkeypatch.setattr(onboarding, "extract_products", fake_extract)
    drafts = client.post(f"/api/businesses/{bakery}/products/import/text", json={"text": "menu"}).json()["drafts"]
    assert drafts[0]["fields"]["price"] == "3.25" and drafts[0]["fields"]["tags"] == "vegetarian"
    assert drafts[1]["fields"]["pricing"] == "quote" and not drafts[1]["errors"]
    assert drafts[2]["errors"] == ["name is missing"]


def rules_by_kind(client, slug):
    return {(r["kind"], r["applies_to"]): r for r in client.get(f"/api/businesses/{slug}").json()["rules"]}


def test_new_business_starts_with_defaults_for_its_type(client, bakery):
    rules = rules_by_kind(client, bakery)
    assert set(rules) == {("payment", ""), ("substitution", ""), ("lead_time", "")}
    assert rules[("lead_time", "")]["summary"] == "All orders: at least 1 day's notice"
    assert rules[("payment", "")]["source_text"] == "Default for a bakery"

    other = client.post("/api/businesses", json={"name": "Odd Shop", "kind": "bike repair",
                                                 "paypal_email": "o@example.com"}).json()["slug"]
    assert set(rules_by_kind(client, other)) == {("payment", ""), ("substitution", "")}


def test_set_rule_replaces_same_kind_and_scope(client, bakery):
    client.post(f"/api/businesses/{bakery}/products",
                json=row(name="Custom cake", category="Custom cakes", price="36"))
    url = f"/api/businesses/{bakery}/rules"
    r = client.post(url, json={"kind": "payment", "applies_to": "Custom cakes",
                               "params": {"style": "deposit_balance", "deposit_percent": 50}})
    assert r.status_code == 201
    assert r.json()["setting"] == "50% deposit when ordering, balance charged at pickup/delivery"
    client.post(url, json={"kind": "payment", "params": {"style": "hold_exact", "buffer_percent": 15}})

    rules = rules_by_kind(client, bakery)
    assert rules[("payment", "")]["params"] == {"style": "hold_exact", "buffer_percent": 15}
    assert len([k for k in rules if k[0] == "payment"]) == 2
    detail = client.get(f"/api/businesses/{bakery}").json()
    assert detail["payment_by_category"] ==         [{"category": "Custom cakes", "style": "deposit_balance", "deposit_percent": 50}]


def test_notes_accumulate(client, bakery):
    url = f"/api/businesses/{bakery}/rules"
    client.post(url, json={"kind": "note", "params": {"text": "Mention our seasonal pies"}})
    client.post(url, json={"kind": "note", "params": {"text": "Ask about allergies"}})
    rules = client.get(f"/api/businesses/{bakery}").json()["rules"]
    assert len([r for r in rules if r["kind"] == "note"]) == 2


def test_invalid_rule_is_rejected(client, bakery):
    url = f"/api/businesses/{bakery}/rules"
    r = client.post(url, json={"kind": "payment", "params": {"style": "deposit_balance"}})
    assert r.status_code == 422 and "deposit_percent" in r.json()["detail"]
    assert client.post(url, json={"kind": "teleport", "params": {}}).status_code == 422


def test_reset_goes_back_to_defaults(client, bakery):
    url = f"/api/businesses/{bakery}/rules"
    client.post(url, json={"kind": "min_order", "params": {"amount": "20"}})
    client.post(url, json={"kind": "lead_time", "params": {"hours": 72}})
    r = client.post(f"/api/businesses/{bakery}/rules/reset")
    assert {(x["kind"], x["params"].get("hours")) for x in r.json()["rules"]} ==         {("payment", None), ("substitution", None), ("lead_time", 24)}


def test_ai_rules_are_validated_and_bulk_save_revalidates(client, bakery, monkeypatch):
    async def fake_rules(text, categories):
        return [
            {"kind": "payment", "applies_to": ["Custom cakes"],
             "params": {"style": "deposit_balance", "deposit_percent": 50}, "source_text": "50% deposit on cakes"},
            {"kind": "lead_time", "applies_to": ["Custom cakes"], "params": {"hours": 72}, "source_text": "3 days"},
            {"kind": "payment", "params": {"style": "deposit_balance", "deposit_percent": 150}, "source_text": "bad"},
            {"kind": "teleport", "params": {}, "source_text": "unknown"},
        ]
    monkeypatch.setattr(onboarding, "extract_rules", fake_rules)
    drafts = client.post(f"/api/businesses/{bakery}/rules/draft", json={"text": "policies"}).json()["drafts"]
    assert drafts[1]["summary"] == "Custom cakes: at least 3 days' notice"
    assert [d["error"] is not None for d in drafts] == [False, False, True, True]

    tampered = {"kind": "payment", "applies_to": "", "params": {"style": "deposit_balance", "deposit_percent": 999}}
    r = client.post(f"/api/businesses/{bakery}/rules/bulk", json={"rules": [drafts[0]["rule"], tampered]})
    assert r.json()["saved"] == 1 and len(r.json()["rejected"]) == 1
    rules = client.get(f"/api/businesses/{bakery}").json()["rules"]
    assert ("payment", "Custom cakes") in [(r["kind"], r["applies_to"]) for r in rules]
    assert len(rules) == 4  # three bakery defaults + the confirmed rule


def test_unbuilt_frontend_and_unknown_api(client):
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/setup").status_code == 200  # SPA route (index.html or build hint)
