"""App-wide information the frontend needs."""

from fastapi import APIRouter

from app import ai
from app.config import APP_NAME

router = APIRouter()


@router.get("/meta")
def meta():
    return {"app_name": APP_NAME, "ai_available": ai.available()}
