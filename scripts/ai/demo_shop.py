"""A demo bakery that uses all three payment styles, for testing the assistant.

    DEPS                       the shop in memory (no database), for quick AI tests
    seed(session, email)       the same shop saved in a database, so orders can be paid through PayPal
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlmodel import Session, select  # noqa: E402

from app.ai.tools import ShopDeps  # noqa: E402
from app.models import Business, Product, Rule  # noqa: E402

SLUG = "sweet-crumb-demo"
NAME = "Sweet Crumb Bakery"

_PRODUCTS = [
    # name, category, price $, pricing, unit, tags, notice hours, description
    ("Vanilla cupcakes (12)", "Cupcakes", 24.00, "fixed", "dozen", "nut-free", 0, "Vanilla sponge, buttercream. Made in our nut-free kitchen."),
    ("Chocolate cupcakes (12)", "Cupcakes", 26.00, "fixed", "dozen", "", 0, "Chocolate sponge, ganache."),
    ("Sugar cookies (12)", "Cookies", 9.00, "fixed", "dozen", "nut-free", 0, "Iced sugar cookies."),
    ("Peanut butter cookies (12)", "Cookies", 9.00, "fixed", "dozen", "contains-peanuts", 0, ""),
    ("Custom celebration cake, 6 inch", "Custom cakes", 40.00, "fixed", "each", "", 72, "Serves 8-10. Your choice of flavour and message."),
    ("Custom celebration cake, 8 inch", "Custom cakes", 60.00, "fixed", "each", "", 72, "Serves 14-20."),
    ("Wedding cake", "Custom cakes", 0, "quote", "each", "", 336, "Priced after a consultation."),
    ("Rye bread", "Bread", 4.50, "per_weight", "lb", "", 0, "Baked daily, sold by weight."),
    ("Sourdough", "Bread", 5.00, "per_weight", "lb", "vegan", 0, "Sold by weight."),
    ("Plain bagels (6)", "Breakfast", 6.00, "fixed", "each", "vegan", 0, ""),
    ("Butter croissants (4)", "Breakfast", 7.00, "fixed", "each", "", 0, ""),
]
_RULES = [
    # kind, applies_to, params
    ("payment", "", {"style": "pay_now"}),
    ("payment", "Custom cakes", {"style": "deposit_balance", "deposit_percent": 50}),
    ("payment", "Bread", {"style": "hold_exact", "buffer_percent": 10}),
    ("substitution", "", {"allowed": False}),
]


def _products(business_id: int, with_ids: bool) -> list[Product]:
    return [Product(id=i if with_ids else None, business_id=business_id, name=n, category=c,
                    price_cents=round(p * 100), pricing=pr, unit=u, tags=t, lead_time_hours=h, description=d)
            for i, (n, c, p, pr, u, t, h, d) in enumerate(_PRODUCTS, 1)]


def _rules(business_id: int, with_ids: bool) -> list[Rule]:
    return [Rule(id=i if with_ids else None, business_id=business_id, kind=k, applies_to=a, params_json=json.dumps(p))
            for i, (k, a, p) in enumerate(_RULES, 1)]


BUSINESS = Business(id=1, slug=SLUG, name=NAME, kind="bakery", paypal_email="demo@example.com")
DEPS = ShopDeps(BUSINESS, _products(1, True), _rules(1, True))


def seed(session: Session, paypal_email: str) -> Business:
    """Create the demo bakery in the database (once), paid to `paypal_email`."""
    business = session.exec(select(Business).where(Business.slug == SLUG)).first()
    if business:
        if business.paypal_email != paypal_email:
            business.paypal_email = paypal_email
            session.add(business)
            session.commit()
        return business
    business = Business(slug=SLUG, name=NAME, kind="bakery", paypal_email=paypal_email,
                        description="Family bakery. Custom cakes need 3 days' notice.")
    session.add(business)
    session.flush()
    session.add_all(_products(business.id, False) + _rules(business.id, False))
    session.commit()
    return business
