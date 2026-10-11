"""Rules: set one from the form, reset to defaults, or (Advanced) draft from plain English for review."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.ai import AgentError, onboarding
from app.api.common import categories_of, get_business, products_of, rule_out, rules_of, save_rule
from app.db import get_session
from app.domain.rules import RuleError, default_rules, describe_rule, normalize_rule
from app.models import Rule

router = APIRouter()


class TextIn(BaseModel):
    text: str


class RulesIn(BaseModel):
    rules: list[dict]


class RuleIn(BaseModel):
    kind: str
    applies_to: str = ""
    params: dict


def rule_draft(raw: dict) -> dict:
    try:
        rule = normalize_rule(raw)
        return {"rule": rule, "summary": describe_rule(rule["kind"], rule["applies_to"], rule["params"]),
                "error": None, "source_text": rule["source_text"]}
    except (RuleError, ValueError) as exc:
        return {"rule": None, "summary": None, "error": str(exc), "source_text": str(raw.get("source_text", ""))}


@router.post("/businesses/{slug}/rules/draft")
async def draft_rules(slug: str, body: TextIn, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    if not body.text.strip():
        raise HTTPException(422, "Write your policies first.")
    try:
        raw_rules = await onboarding.extract_rules(body.text, categories_of(products_of(session, business)))
    except AgentError as exc:
        raise HTTPException(502, str(exc))
    if not raw_rules:
        raise HTTPException(422, "No rules found in that text.")
    return {"drafts": [rule_draft(r) for r in raw_rules]}


@router.post("/businesses/{slug}/rules/bulk")
def save_rules(slug: str, body: RulesIn, session: Session = Depends(get_session)):
    """Save confirmed rules. Each is re-validated (the client is never trusted) and replaces
    the current setting of the same kind and scope."""
    business = get_business(session, slug)
    saved, rejected = 0, []
    for raw in body.rules:
        try:
            save_rule(session, business, normalize_rule(raw))
            saved += 1
        except (RuleError, ValueError) as exc:
            rejected.append(str(exc))
    session.commit()
    return {"saved": saved, "rejected": rejected}


@router.post("/businesses/{slug}/rules", status_code=201)
def set_rule(slug: str, body: RuleIn, session: Session = Depends(get_session)):
    """Set one rule from the form (no AI). Replaces the current setting of the same kind and scope."""
    business = get_business(session, slug)
    try:
        rule = normalize_rule(body.model_dump())
    except (RuleError, ValueError) as exc:
        raise HTTPException(422, str(exc))
    saved = save_rule(session, business, rule)
    session.commit()
    session.refresh(saved)
    return rule_out(saved)


@router.post("/businesses/{slug}/rules/reset")
def reset_rules(slug: str, session: Session = Depends(get_session)):
    """Remove every rule and go back to the defaults for this business type."""
    business = get_business(session, slug)
    for existing in rules_of(session, business):
        session.delete(existing)
    session.flush()
    for rule in default_rules(business.kind):
        save_rule(session, business, rule)
    session.commit()
    return {"rules": [rule_out(r) for r in rules_of(session, business)]}


@router.delete("/businesses/{slug}/rules/{rule_id}", status_code=204)
def delete_rule(slug: str, rule_id: int, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    rule = session.get(Rule, rule_id)
    if rule and rule.business_id == business.id:
        session.delete(rule)
        session.commit()
