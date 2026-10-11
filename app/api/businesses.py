"""Creating businesses and reading everything the setup screens need."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.common import (business_out, categories_of, get_business, product_out, products_of, rule_out,
                            rules_of, save_rule)
from app.db import get_session
from app.domain.rules import default_rules, payment_style_for
from app.models import Business

router = APIRouter()


class BusinessIn(BaseModel):
    name: str
    kind: str = ""
    description: str = ""
    paypal_email: str


def unique_slug(session: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "business"
    slug, n = base, 2
    while session.exec(select(Business).where(Business.slug == slug)).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


@router.get("/businesses")
def list_businesses(session: Session = Depends(get_session)):
    businesses = session.exec(select(Business).order_by(Business.created_at.desc())).all()
    return [business_out(b) for b in businesses]


@router.post("/businesses", status_code=201)
def create_business(body: BusinessIn, session: Session = Depends(get_session)):
    data = {k: v.strip() for k, v in body.model_dump().items()}
    if not data["name"] or not data["paypal_email"]:
        raise HTTPException(422, "Business name and PayPal email are required.")
    business = Business(slug=unique_slug(session, data["name"]), **data)
    session.add(business)
    session.flush()
    for rule in default_rules(business.kind):
        save_rule(session, business, rule)
    session.commit()
    return business_out(business)


@router.get("/businesses/{slug}")
def business_detail(slug: str, session: Session = Depends(get_session)):
    """Everything the setup screens need in one call."""
    business = get_business(session, slug)
    products = products_of(session, business)
    rules = rules_of(session, business)
    categories = categories_of(products)
    return {
        "business": business_out(business),
        "products": [product_out(p) for p in products],
        "rules": [rule_out(r) for r in rules],
        "categories": categories,
        "payment_by_category": [{"category": c, **payment_style_for(rules, c)} for c in categories or [""]],
    }
