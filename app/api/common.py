"""Lookups, serializers and saving shared by the API routers."""

import json

from fastapi import HTTPException
from sqlmodel import Session, select

from app.domain.rules import describe_rule, describe_setting, same_setting
from app.models import Business, Product, Rule


def get_business(session: Session, slug: str) -> Business:
    business = session.exec(select(Business).where(Business.slug == slug)).first()
    if not business:
        raise HTTPException(404, "Business not found")
    return business


def products_of(session, business):
    return session.exec(select(Product).where(Product.business_id == business.id)
                        .order_by(Product.category, Product.name)).all()


def rules_of(session, business):
    return session.exec(select(Rule).where(Rule.business_id == business.id).order_by(Rule.id)).all()


def categories_of(products) -> list[str]:
    return sorted({p.category for p in products if p.category})


def business_out(b: Business) -> dict:
    return {"slug": b.slug, "name": b.name, "kind": b.kind, "description": b.description,
            "paypal_email": b.paypal_email, "currency": b.currency}


def product_out(p: Product) -> dict:
    return p.model_dump(exclude={"business_id"})


def rule_out(r: Rule) -> dict:
    return {"id": r.id, "kind": r.kind, "applies_to": r.applies_to, "params": r.params,
            "source_text": r.source_text, "summary": describe_rule(r.kind, r.applies_to, r.params),
            "setting": describe_setting(r.kind, r.params)}


def save_rule(session, business, rule: dict) -> Rule:
    """Save a validated rule, replacing the business's current setting of the same kind and scope."""
    for existing in rules_of(session, business):
        if same_setting(existing.kind, existing.applies_to, rule["kind"], rule["applies_to"]):
            session.delete(existing)
    saved = Rule(business_id=business.id, kind=rule["kind"], applies_to=rule["applies_to"],
                 params_json=json.dumps(rule["params"]), source_text=rule["source_text"])
    session.add(saved)
    session.flush()
    return saved
