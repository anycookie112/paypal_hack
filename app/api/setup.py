"""The group's setup: common items, categories, budgets and settings. Everyone reads; app/core/setup.py lets only
admins change anything. Every change returns the whole page's data again, so the page just shows it."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import MemberDep, SessionDep, group_of
from app.core import setup

router = APIRouter()


class CategoryIn(BaseModel):
    name: str
    monthly_budget_cents: int = 0


class CategoryChange(BaseModel):
    name: str | None = None
    monthly_budget_cents: int | None = None


class ItemIn(BaseModel):
    category_id: int
    name: str
    aliases: list[str] = []
    cap_cents: int
    restock_days: int = 14
    proof: str = "receipt"


class ItemChange(BaseModel):
    category_id: int | None = None
    name: str | None = None
    aliases: list[str] | None = None
    cap_cents: int | None = None
    restock_days: int | None = None
    proof: str | None = None
    active: bool | None = None  # False switches it off


class SettingsChange(BaseModel):
    name: str | None = None
    monthly_budget_cents: int | None = None
    values: dict[str, object] = {}  # setting key -> new value


@router.get("/catalog", response_model=setup.Catalog)
def catalog(session: SessionDep, member: MemberDep):
    return setup.catalog(session, group_of(session, member))


@router.post("/categories", response_model=setup.Catalog)
def add_category(body: CategoryIn, session: SessionDep, member: MemberDep):
    setup.add_category(session, member, **body.model_dump())
    return setup.catalog(session, group_of(session, member))


@router.patch("/categories/{category_id}", response_model=setup.Catalog)
def update_category(category_id: int, body: CategoryChange, session: SessionDep, member: MemberDep):
    setup.update_category(session, member, category_id, **body.model_dump(exclude_unset=True))
    return setup.catalog(session, group_of(session, member))


@router.post("/items", response_model=setup.Catalog)
def add_item(body: ItemIn, session: SessionDep, member: MemberDep):
    setup.add_item(session, member, **body.model_dump())
    return setup.catalog(session, group_of(session, member))


@router.patch("/items/{item_id}", response_model=setup.Catalog)
def update_item(item_id: int, body: ItemChange, session: SessionDep, member: MemberDep):
    setup.update_item(session, member, item_id, **body.model_dump(exclude_unset=True))
    return setup.catalog(session, group_of(session, member))


@router.get("/settings", response_model=setup.GroupSettings)
def settings(session: SessionDep, member: MemberDep):
    return setup.group_settings(session, group_of(session, member))


@router.patch("/settings", response_model=setup.GroupSettings)
def save_settings(body: SettingsChange, session: SessionDep, member: MemberDep):
    return setup.save_settings(session, member, **body.model_dump(exclude_unset=True))
