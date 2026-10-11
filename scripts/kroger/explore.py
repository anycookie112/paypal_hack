"""Kroger Public API spike: can we plan groceries with live store prices?

Questions:
  1. Do client-credential tokens work in production for Products + Locations?
  2. Do products return real prices (regular + promo) for a specific store?
  3. How are items sold by weight (produce, meat) represented?
  4. Is stock/availability returned?

Usage:
  python scripts/kroger/explore.py [zip] [term ...]
  python scripts/kroger/explore.py 45202 "chicken breast" eggs bananas pasta
Data is fetched live and printed; nothing is stored.
"""

import os
import sys

import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_URL = "https://api.kroger.com/v1"
CLIENT_ID = os.environ.get("KROGER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("KROGER_CLIENT_SECRET")


def get_token():
    r = requests.post(
        f"{BASE_URL}/connect/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials", "scope": "product.compact"},
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Token request failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


def get(token, path, params):
    r = requests.get(f"{BASE_URL}{path}", params=params,
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        print(f"!! GET {path} -> HTTP {r.status_code} {r.text[:300]}")
        return None
    return r.json()


def main():
    zip_code = sys.argv[1] if len(sys.argv) > 1 else "45202"
    terms = sys.argv[2:] or ["chicken breast", "eggs", "bananas", "pasta", "ground beef"]

    token = get_token()
    print("Token OK\n")

    locs = get(token, "/locations", {"filter.zipCode.near": zip_code, "filter.limit": 3})
    if not locs or not locs.get("data"):
        sys.exit("No locations returned")
    for loc in locs["data"]:
        addr = loc.get("address", {})
        print(f"Store {loc['locationId']}: {loc.get('chain')} {loc.get('name')} - "
              f"{addr.get('addressLine1')}, {addr.get('city')} {addr.get('state')}")
    location_id = locs["data"][0]["locationId"]
    print(f"\nUsing store {location_id}\n")

    for term in terms:
        res = get(token, "/products", {"filter.term": term, "filter.locationId": location_id, "filter.limit": 4})
        print(f"=== '{term}'")
        if not res:
            continue
        for p in res.get("data", []):
            for item in p.get("items", [])[:1]:
                price = item.get("price") or {}
                regular, promo = price.get("regular"), price.get("promo")
                promo_txt = f"  PROMO ${promo}" if promo and promo != regular else ""
                stock = (item.get("inventory") or {}).get("stockLevel", "n/a")
                print(f"  {p.get('description')[:55]:55} | {item.get('size', ''):12} | "
                      f"soldBy={item.get('soldBy', 'n/a'):6} | ${regular}{promo_txt} | stock={stock}")
        print()


if __name__ == "__main__":
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("Missing KROGER_CLIENT_ID / KROGER_CLIENT_SECRET in .env")
    main()
