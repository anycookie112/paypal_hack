"""JSON API for the React frontend, under /api."""

from fastapi import APIRouter

from app.api import businesses, meta, orders, products, rules

router = APIRouter(prefix="/api")
for module in (meta, businesses, products, rules, orders):
    router.include_router(module.router)
