"""Products: import (CSV or AI from pasted text) for review, then save; add and remove one."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.ai import AgentError, onboarding
from app.api.common import get_business, product_out
from app.db import get_session
from app.domain.catalog import CSV_COLUMNS, normalize_product, parse_csv
from app.models import Product

router = APIRouter()


class TextIn(BaseModel):
    text: str


class ProductsIn(BaseModel):
    products: list[dict]


def product_draft(raw: dict) -> dict:
    """A reviewable row: form-ready strings plus any validation errors."""
    product, errors = normalize_product(raw)
    fields = {col: "" if raw.get(col) is None else str(raw.get(col)) for col in CSV_COLUMNS}
    fields["tags"] = product["tags"]
    if "price" not in " ".join(errors):
        fields["price"] = f"{product['price_cents'] / 100:.2f}"
    fields["pricing"] = product["pricing"]
    fields["unit"] = product["unit"]
    return {"fields": fields, "errors": errors}


@router.post("/businesses/{slug}/products/import/csv")
async def import_csv(slug: str, file: UploadFile, session: Session = Depends(get_session)):
    get_business(session, slug)
    try:
        rows = parse_csv((await file.read()).decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not rows:
        raise HTTPException(422, "The file has no product rows.")
    return {"drafts": [product_draft(r) for r in rows]}


@router.post("/businesses/{slug}/products/import/text")
async def import_text(slug: str, body: TextIn, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    if not body.text.strip():
        raise HTTPException(422, "Paste your menu or price list first.")
    try:
        raw_products = await onboarding.extract_products(business.kind, body.text)
    except AgentError as exc:
        raise HTTPException(502, str(exc))
    if not raw_products:
        raise HTTPException(422, "No products found in that text.")
    return {"drafts": [product_draft(r) for r in raw_products]}


@router.post("/businesses/{slug}/products/bulk")
def save_products(slug: str, body: ProductsIn, session: Session = Depends(get_session)):
    """Save reviewed rows. If any row is invalid, nothing is saved and every row comes back with its errors."""
    business = get_business(session, slug)
    drafts = [product_draft({col: row.get(col, "") for col in CSV_COLUMNS}) for row in body.products]
    if any(d["errors"] for d in drafts):
        return JSONResponse({"detail": "Fix the highlighted rows, or remove them, then save again.",
                             "drafts": drafts}, status_code=422)
    for d in drafts:
        product, _ = normalize_product(d["fields"])
        session.add(Product(business_id=business.id, **product))
    session.commit()
    return {"saved": len(drafts)}


@router.post("/businesses/{slug}/products", status_code=201)
def add_product(slug: str, body: dict, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    product, errors = normalize_product({col: body.get(col, "") for col in CSV_COLUMNS})
    if errors:
        raise HTTPException(422, "; ".join(errors))
    p = Product(business_id=business.id, **product)
    session.add(p)
    session.commit()
    session.refresh(p)
    return product_out(p)


@router.delete("/businesses/{slug}/products/{product_id}", status_code=204)
def delete_product(slug: str, product_id: int, session: Session = Depends(get_session)):
    business = get_business(session, slug)
    product = session.get(Product, product_id)
    if product and product.business_id == business.id:
        session.delete(product)
        session.commit()
