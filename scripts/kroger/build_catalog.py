"""Build a realistic test catalog from the Kroger Products API, as a CSV our product import accepts.

Real product names, sizes and store prices, so the AI reliability test runs against a realistic
catalog instead of one we made up. Output goes to data/ (gitignored): it's test data, not shipped.

Usage:
  python scripts/kroger/build_catalog.py [bakery|grocer] [per_term]
  python scripts/kroger/build_catalog.py bakery 9      # ~100 products -> data/kroger_bakery.csv
"""

import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from explore import CLIENT_ID, CLIENT_SECRET, get, get_token  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOCATION_ID = os.environ.get("KROGER_LOCATION_ID", "01400513")

# search term -> category in our catalog
PRESETS = {
    "bakery": {
        "birthday cake": "Cakes", "sheet cake": "Cakes", "cheesecake": "Cakes",
        "cupcakes": "Cupcakes", "cookies bakery": "Cookies", "brownies": "Brownies & bars",
        "bread loaf bakery": "Bread", "dinner rolls": "Bread", "bagels": "Breakfast pastries",
        "muffins": "Breakfast pastries", "cinnamon rolls": "Breakfast pastries", "danish": "Breakfast pastries",
        "croissants": "Breakfast pastries", "donuts": "Donuts", "pie": "Pies",
    },
    "grocer": {
        "bananas": "Produce", "apples": "Produce", "tomatoes": "Produce", "potatoes": "Produce",
        "chicken breast": "Meat", "ground beef": "Meat", "pork chops": "Meat", "salmon": "Seafood",
        "milk": "Dairy", "eggs": "Dairy", "cheddar cheese": "Dairy", "bread": "Bakery", "pasta": "Pantry",
    },
}

# Kroger's own department, to drop search noise (lip balm, greeting cards, ...)
DEPARTMENTS = {"bakery": {"Bakery"}, "grocer": None}

# words in a product name -> tags the assistant can rely on (Kroger's compact scope has no allergen data)
TAG_WORDS = {"gluten free": "gluten-free", "sugar free": "sugar-free", "vegan": "vegan",
             "organic": "organic", "keto": "keto", "nut": "contains-nuts", "pecan": "contains-nuts",
             "almond": "contains-nuts", "walnut": "contains-nuts", "peanut": "contains-peanuts"}


def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"[®™©]", "", text or "")).strip()


def to_row(product, category, departments):
    item = (product.get("items") or [{}])[0]
    price = (item.get("price") or {}).get("regular")
    if not price:
        return None  # not sold at this store
    if departments and not departments & set(product.get("categories") or []):
        return None
    name = clean(product.get("description"))
    if name.isupper():
        return None  # internal abbreviations like "BFR HALLOWEEN CPCKS 12CT"
    by_weight = item.get("soldBy") == "WEIGHT"
    lowered = name.lower()
    tags = sorted({tag for word, tag in TAG_WORDS.items()
                   if re.search(f"(?<![a-z]){word}s?(?![a-z])", lowered)})  # whole words: "donut" isn't "nut"
    details = [clean(product.get("brand")), clean(item.get("size"))]
    return {
        "name": name,
        "category": category,
        "price": f"{price:.2f}",
        "pricing": "per_weight" if by_weight else "fixed",
        "unit": "lb" if by_weight else "each",
        "tags": ", ".join(tags),
        "lead_time_hours": "0",
        "description": ", ".join(d for d in details if d),
    }


def main():
    preset = sys.argv[1] if len(sys.argv) > 1 else "bakery"
    per_term = int(sys.argv[2]) if len(sys.argv) > 2 else 9
    if preset not in PRESETS:
        sys.exit(f"Unknown preset '{preset}'. Choose: {', '.join(PRESETS)}")

    token = get_token()
    rows, seen = [], set()
    for term, category in PRESETS[preset].items():
        res = get(token, "/products", {"filter.term": term, "filter.locationId": LOCATION_ID,
                                       "filter.limit": 50})
        added = 0
        for product in (res or {}).get("data", []):
            row = to_row(product, category, DEPARTMENTS[preset])
            if not row or product["productId"] in seen or row["name"].lower() in seen:
                continue
            seen.update({product["productId"], row["name"].lower()})
            rows.append(row)
            added += 1
            if added == per_term:
                break
        print(f"{term:20} -> {added:2} products ({category})")

    out = ROOT / "data" / f"kroger_{preset}.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} products written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("Missing KROGER_CLIENT_ID / KROGER_CLIENT_SECRET in .env")
    main()
