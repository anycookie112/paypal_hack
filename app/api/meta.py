"""Options the frontend needs to render forms."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app import ai
from app.config import APP_NAME
from app.domain.catalog import CSV_COLUMNS, CSV_TEMPLATE, PRICING
from app.domain.rules import PAYMENT_STYLES

router = APIRouter()

BUSINESS_KINDS = ["bakery", "grocer", "florist", "caterer", "café", "butcher", "deli"]


@router.get("/meta")
def meta():
    return {"app_name": APP_NAME, "ai_available": ai.available(), "business_kinds": BUSINESS_KINDS,
            "pricing": PRICING, "payment_styles": PAYMENT_STYLES, "csv_columns": CSV_COLUMNS}


@router.get("/products-template.csv")
def products_template():
    return PlainTextResponse(CSV_TEMPLATE, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=products-template.csv"})
