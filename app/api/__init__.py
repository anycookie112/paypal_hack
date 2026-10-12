"""JSON API for the React frontend, under /api. Routes stay thin: decisions and money live in app/core."""

from fastapi import APIRouter

from app.api import meta

router = APIRouter(prefix="/api")
for module in (meta,):
    router.include_router(module.router)
