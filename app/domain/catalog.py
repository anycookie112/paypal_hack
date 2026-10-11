"""Products: one normalized shape for CSV rows, AI-extracted drafts and manual entries."""

import csv
import io

from app.domain.money import to_cents

PRICING = {
    "fixed": "Fixed price",
    "per_weight": "Priced by weight",
    "quote": "Price on request",
}
CSV_COLUMNS = ["name", "category", "price", "pricing", "unit", "tags", "lead_time_hours", "description"]
CSV_TEMPLATE = (
    ",".join(CSV_COLUMNS) + "\n"
    "Sourdough loaf,Bread,6.50,fixed,each,vegan,0,Naturally leavened\n"
    "Vanilla cupcake,Cupcakes,3.00,fixed,each,nut-free,0,\n"
    "Custom celebration cake (6 inch),Custom cakes,36.00,fixed,each,,72,Choose flavor and message\n"
    "Bananas,Produce,0.59,per_weight,lb,,0,\n"
)


def normalize_product(raw: dict) -> tuple[dict, list[str]]:
    """Returns (clean product fields, errors). Errors mean the row can't be saved as-is."""
    errors = []
    name = str(raw.get("name") or "").strip()
    if not name:
        errors.append("name is missing")

    pricing = str(raw.get("pricing") or "fixed").strip().lower().replace(" ", "_")
    if pricing not in PRICING:
        errors.append(f"pricing must be one of: {', '.join(PRICING)}")
        pricing = "fixed"

    price_cents = 0
    try:
        price_cents = to_cents(raw.get("price") if raw.get("price") not in (None, "") else
                               ("0" if pricing == "quote" else None))
    except ValueError as exc:
        errors.append(str(exc))

    try:
        lead_time_hours = int(float(raw.get("lead_time_hours") or 0))
    except (TypeError, ValueError):
        lead_time_hours = 0
        errors.append("lead time must be a number of hours")

    tags = raw.get("tags") or ""
    if isinstance(tags, list):
        tags = ",".join(str(t) for t in tags)
    tags = ",".join(t.strip().lower() for t in str(tags).split(",") if t.strip())

    product = {
        "name": name,
        "category": str(raw.get("category") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "price_cents": price_cents,
        "pricing": pricing,
        "unit": str(raw.get("unit") or ("lb" if pricing == "per_weight" else "each")).strip(),
        "tags": tags,
        "lead_time_hours": lead_time_hours,
    }
    return product, errors


def parse_csv(text: str) -> list[dict]:
    """Rows as raw dicts keyed by CSV_COLUMNS (unknown columns ignored, missing ones blank)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "name" not in [f.strip().lower() for f in reader.fieldnames]:
        raise ValueError("The file needs a header row with at least a 'name' column. Download the template.")
    rows = []
    for row in reader:
        clean = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if any(clean.values()):
            rows.append({col: clean.get(col, "") for col in CSV_COLUMNS})
    return rows
