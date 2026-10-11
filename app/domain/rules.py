"""Business rules: the closed set of rule kinds code knows how to enforce.

The AI only proposes rules. Every rule is validated here, and the summary shown to the
owner is generated from the validated params, so what the owner confirms is exactly
what code will enforce.
"""

from app.domain.money import cents_to_str, to_cents

PAYMENT_STYLES = {
    "pay_now": "Pay in full when ordering",
    "hold_exact": "Hold an amount when ordering, then charge the exact final amount",
    "deposit_balance": "Charge a deposit when ordering and the balance at pickup/delivery",
}

RULE_KINDS = {
    "payment": "How customers pay. params: style (pay_now | hold_exact | deposit_balance), "
               "deposit_percent (1-100, for deposit_balance), buffer_percent (0-50, extra hold for hold_exact; "
               "0 if the owner holds the exact quoted amount)",
    "lead_time": "Minimum notice before pickup/delivery. params: hours",
    "min_order": "Minimum total for every order. params: amount (dollars)",
    "delivery": "Delivery offered. params: radius_miles, fee (dollars), free_over (dollars, optional), "
                "min_order (dollars, optional: minimum order total for delivery only)",
    "capacity": "Maximum per day. params: max_per_day",
    "substitution": "Whether out-of-stock items may be substituted. params: allowed (true/false)",
    "note": "Anything else, shown to the assistant but not enforced by code. params: text",
}


# Settings every new business starts with, by business type. The owner changes them in the
# rules form; anything not listed is off (no minimum, pickup only, no daily limit, ...).
BASE_DEFAULTS = [
    {"kind": "payment", "params": {"style": "pay_now"}},
    {"kind": "substitution", "params": {"allowed": False}},
]
DEFAULTS_BY_KIND = {
    "bakery": [{"kind": "lead_time", "params": {"hours": 24}}],
    "grocer": [{"kind": "payment", "params": {"style": "hold_exact", "buffer_percent": 15}},
               {"kind": "substitution", "params": {"allowed": True}}],
    "butcher": [{"kind": "payment", "params": {"style": "hold_exact", "buffer_percent": 10}},
                {"kind": "lead_time", "params": {"hours": 24}}],
    "florist": [{"kind": "lead_time", "params": {"hours": 24}},
                {"kind": "substitution", "params": {"allowed": True}}],
    "caterer": [{"kind": "payment", "params": {"style": "deposit_balance", "deposit_percent": 50}},
                {"kind": "lead_time", "params": {"hours": 72}},
                {"kind": "min_order", "params": {"amount": 100}}],
}


class RuleError(ValueError):
    pass


def _int(params, key, lo, hi, default=None):
    value = params.get(key, default)
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        raise RuleError(f"'{key}' must be a number")
    if not lo <= value <= hi:
        raise RuleError(f"'{key}' must be between {lo} and {hi}")
    return value


def normalize_scope(applies_to) -> str:
    """A rule applies to the whole business ("") or to one or more categories ("Custom cakes, Wedding cakes")."""
    if isinstance(applies_to, (list, tuple)):
        names = [str(a).strip() for a in applies_to]
    else:
        names = str(applies_to or "").split(",")
    return ", ".join(n for n in (x.strip() for x in names) if n)


def scope_includes(applies_to: str, category: str) -> bool:
    return (category or "").strip().lower() in [c.strip().lower() for c in applies_to.split(",") if c.strip()]


def same_setting(kind: str, applies_to: str, other_kind: str, other_applies_to: str) -> bool:
    """One setting per rule kind and scope: a new one replaces the old. Notes can repeat."""
    def key(scope):
        return sorted(c.strip().lower() for c in scope.split(",") if c.strip())
    return kind == other_kind and kind != "note" and key(applies_to) == key(other_applies_to)


def default_rules(business_kind: str) -> list[dict]:
    """Validated starting rules for a business type (unknown types get the base set)."""
    preset = DEFAULTS_BY_KIND.get(business_kind.strip().lower(), [])
    rules = {r["kind"]: r for r in BASE_DEFAULTS}
    rules.update({r["kind"]: r for r in preset})
    label = f"Default for a {business_kind.strip().lower()}" if preset else "Default"
    return [normalize_rule({**r, "source_text": label}) for r in rules.values()]


def normalize_rule(raw: dict) -> dict:
    """Validate an AI-proposed (or edited) rule. Returns {kind, applies_to, params, source_text}."""
    kind = str(raw.get("kind", "")).strip()
    if kind not in RULE_KINDS:
        raise RuleError(f"unknown rule kind '{kind}'")
    params = raw.get("params") or {}
    applies_to = normalize_scope(raw.get("applies_to"))
    out = {}

    if kind == "payment":
        style = params.get("style")
        if style not in PAYMENT_STYLES:
            raise RuleError(f"payment style must be one of {', '.join(PAYMENT_STYLES)}")
        out["style"] = style
        if style == "deposit_balance":
            out["deposit_percent"] = _int(params, "deposit_percent", 1, 100)
        if style == "hold_exact":
            out["buffer_percent"] = _int(params, "buffer_percent", 0, 50, default=10)
    elif kind == "lead_time":
        out["hours"] = _int(params, "hours", 0, 24 * 60)
    elif kind == "min_order":
        out["amount_cents"] = to_cents(params.get("amount"))
    elif kind == "delivery":
        radius = params.get("radius_miles")
        try:
            out["radius_miles"] = float(radius)
        except (TypeError, ValueError):
            raise RuleError("'radius_miles' must be a number")
        out["fee_cents"] = to_cents(params.get("fee", 0))
        if params.get("free_over") not in (None, "", 0):
            out["free_over_cents"] = to_cents(params["free_over"])
        if params.get("min_order") not in (None, "", 0):
            out["min_order_cents"] = to_cents(params["min_order"])
    elif kind == "capacity":
        out["max_per_day"] = _int(params, "max_per_day", 1, 10_000)
    elif kind == "substitution":
        allowed = params.get("allowed")
        if isinstance(allowed, str):
            allowed = allowed.strip().lower() in ("true", "yes", "1")
        out["allowed"] = bool(allowed)
    elif kind == "note":
        text = str(params.get("text", "")).strip()
        if not text:
            raise RuleError("note text is empty")
        out["text"] = text

    return {"kind": kind, "applies_to": applies_to, "params": out,
            "source_text": str(raw.get("source_text", "")).strip()}


def describe_rule(kind: str, applies_to: str, params: dict) -> str:
    """Plain-language summary generated from validated params (never from AI prose)."""
    scope = f"{applies_to}: " if applies_to else "All orders: "
    return scope + describe_setting(kind, params)


def describe_setting(kind: str, params: dict) -> str:
    """The setting itself, without the scope ("50% deposit when ordering, ...")."""
    if kind == "payment":
        style = params["style"]
        if style == "pay_now":
            return "paid in full when ordering"
        if style == "deposit_balance":
            return f"{params['deposit_percent']}% deposit when ordering, balance charged at pickup/delivery"
        held = (f"the estimated total + {params['buffer_percent']}%" if params["buffer_percent"]
                else "the quoted total")
        return f"hold {held} when ordering, then charge the exact final amount and release the rest"
    if kind == "lead_time":
        hours = params["hours"]
        if hours == 0:
            return "no notice needed"
        count, unit = (hours // 24, "day") if hours % 24 == 0 else (hours, "hour")
        return f"at least {count} {unit}'s notice" if count == 1 else f"at least {count} {unit}s' notice"
    if kind == "min_order":
        return f"minimum order {cents_to_str(params['amount_cents'])}"
    if kind == "delivery":
        text = f"delivery within {params['radius_miles']:g} miles for {cents_to_str(params['fee_cents'])}"
        if "free_over_cents" in params:
            text += f", free over {cents_to_str(params['free_over_cents'])}"
        if "min_order_cents" in params:
            text += f"; minimum order {cents_to_str(params['min_order_cents'])} for delivery"
        return text
    if kind == "capacity":
        return f"at most {params['max_per_day']} per day"
    if kind == "substitution":
        return ("substitutions allowed" if params["allowed"] else "no substitutions")
    if kind == "note":
        return params["text"] + " (guidance only, not enforced)"
    return kind


def payment_style_for(rules, category: str) -> dict:
    """The payment rule that applies to a category: category-specific beats business-wide.
    Defaults to pay_now when the owner hasn't set one."""
    business_wide = None
    for rule in rules:
        if rule.kind != "payment":
            continue
        if rule.applies_to and scope_includes(rule.applies_to, category):
            return rule.params
        if not rule.applies_to:
            business_wide = rule.params
    return business_wide or {"style": "pay_now"}
