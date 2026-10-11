import pytest

from app.domain.money import to_cents
from app.models import Rule
from app.domain.rules import RuleError, default_rules, describe_rule, normalize_rule, payment_style_for, same_setting


@pytest.mark.parametrize("value,cents", [("12.50", 1250), ("$1,200", 120000), (0.59, 59), ("3", 300)])
def test_to_cents(value, cents):
    assert to_cents(value) == cents


@pytest.mark.parametrize("value", ["", None, "abc", "-1"])
def test_to_cents_rejects(value):
    with pytest.raises(ValueError):
        to_cents(value)


def test_hold_exact_defaults_buffer():
    rule = normalize_rule({"kind": "payment", "applies_to": "Produce", "params": {"style": "hold_exact"}})
    assert rule["params"] == {"style": "hold_exact", "buffer_percent": 10}
    assert "exact final amount" in describe_rule("payment", "Produce", rule["params"])


def test_delivery_rule_summary():
    rule = normalize_rule({"kind": "delivery", "params": {"radius_miles": 5, "fee": "8", "free_over": 75}})
    assert describe_rule("delivery", "", rule["params"]) == \
        "All orders: delivery within 5 miles for $8.00, free over $75.00"


@pytest.mark.parametrize("raw", [
    {"kind": "payment", "params": {"style": "crypto"}},
    {"kind": "capacity", "params": {"max_per_day": 0}},
    {"kind": "note", "params": {"text": "  "}},
    {"kind": "unknown"},
])
def test_invalid_rules_rejected(raw):
    with pytest.raises((RuleError, ValueError)):
        normalize_rule(raw)


def test_payment_style_precedence():
    rules = [
        Rule(business_id=1, kind="payment", applies_to="", params_json='{"style": "hold_exact", "buffer_percent": 15}'),
        Rule(business_id=1, kind="payment", applies_to="Custom cakes",
             params_json='{"style": "deposit_balance", "deposit_percent": 50}'),
    ]
    assert payment_style_for(rules, "custom cakes")["style"] == "deposit_balance"
    assert payment_style_for(rules, "Bread")["style"] == "hold_exact"
    assert payment_style_for([], "Bread") == {"style": "pay_now"}


def test_rule_can_apply_to_several_categories():
    rule = normalize_rule({"kind": "substitution", "applies_to": ["Custom cakes", " Wedding cakes "],
                           "params": {"allowed": False}})
    assert rule["applies_to"] == "Custom cakes, Wedding cakes"
    assert describe_rule("substitution", rule["applies_to"], rule["params"]) == \
        "Custom cakes, Wedding cakes: no substitutions"
    rules = [Rule(business_id=1, kind="payment", applies_to="Custom cakes, Wedding cakes",
                  params_json='{"style": "deposit_balance", "deposit_percent": 50}')]
    assert payment_style_for(rules, "wedding cakes")["style"] == "deposit_balance"
    assert payment_style_for(rules, "Bread") == {"style": "pay_now"}


def test_delivery_minimum_is_part_of_delivery_rule():
    rule = normalize_rule({"kind": "delivery", "params": {"radius_miles": 5, "fee": 8, "min_order": 20}})
    assert describe_rule("delivery", "", rule["params"]) == \
        "All orders: delivery within 5 miles for $8.00; minimum order $20.00 for delivery"


def test_notice_wording():
    assert describe_rule("lead_time", "", {"hours": 72}) == "All orders: at least 3 days' notice"
    assert describe_rule("lead_time", "", {"hours": 1}) == "All orders: at least 1 hour's notice"
    assert describe_rule("lead_time", "", {"hours": 30}) == "All orders: at least 30 hours' notice"


def test_presets_override_base_defaults():
    grocer = {r["kind"]: r["params"] for r in default_rules(" Grocer ")}
    assert grocer["payment"] == {"style": "hold_exact", "buffer_percent": 15}
    assert grocer["substitution"] == {"allowed": True}


def test_same_setting_ignores_case_and_order():
    assert same_setting("payment", "Cakes, Pies", "payment", "pies, cakes")
    assert not same_setting("payment", "Cakes", "payment", "")
    assert not same_setting("note", "", "note", "")
