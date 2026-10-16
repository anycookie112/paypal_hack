"""JSON API for the React frontend, under /api. Routes stay thin: decisions and money live in app/core."""

from fastapi import APIRouter

from app.api import claims, members, meta, setup
from app.api.deps import add_error_handlers  # noqa: F401  (used by app/main.py)

router = APIRouter(prefix="/api")
for module in (meta, members, setup, claims):
    router.include_router(module.router)
