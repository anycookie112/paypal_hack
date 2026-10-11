"""Owner onboarding (Advanced): turn pasted text into draft products and rules.

Output is only a draft. Products go through catalog.normalize_product and rules through
rules.normalize_rule before the owner sees them, and nothing is saved until they confirm.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from app.ai.base import BaseAgent
from app.domain.catalog import PRICING
from app.domain.rules import RULE_KINDS


class DraftProduct(BaseModel):
    name: str
    category: str = ""
    price: float = 0
    pricing: str = Field("fixed", description=f"One of: {', '.join(PRICING)}")
    unit: str = "each"
    tags: list[str] = []
    lead_time_hours: int = 0
    description: str = ""


class DraftRule(BaseModel):
    kind: str = Field(description=f"One of: {', '.join(RULE_KINDS)}")
    applies_to: list[str] = []
    params: dict[str, Any] = {}
    source_text: str = ""


class ProductExtractor(BaseAgent[None, list[DraftProduct]]):
    name = "product_extractor"
    output_type = list[DraftProduct]
    prompt = "extract_products"
    max_requests = 3
    max_reply_tokens = 12000  # a long menu is one long reply


class RuleExtractor(BaseAgent[None, list[DraftRule]]):
    name = "rule_extractor"
    output_type = list[DraftRule]
    prompt = "extract_rules"
    max_requests = 3
    max_reply_tokens = 6000


async def extract_products(business_kind: str, text: str) -> list[dict]:
    prompt = f"Business type: {business_kind or 'unknown'}\n\nMenu or price list:\n{text}"
    result = await ProductExtractor().run(prompt)
    return [p.model_dump() for p in result.output]


async def extract_rules(text: str, categories: list[str]) -> list[dict]:
    prompt = (f"Rule kinds and their params:\n{json.dumps(RULE_KINDS, indent=1)}\n"
              f"Product categories: {json.dumps(categories)}\n\nOwner's policies:\n{text}")
    result = await RuleExtractor().run(prompt)
    return [r.model_dump() for r in result.output]
