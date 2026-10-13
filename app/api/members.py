"""The people in the group: for the "View as" switcher, and who's signed in."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import MemberDep, SessionDep
from app.models import Member

router = APIRouter()


class MemberOut(BaseModel):
    id: int
    name: str
    role: str  # member | approver | admin


@router.get("/members", response_model=list[MemberOut])
def members(session: SessionDep):
    """Everyone active, admins first: the switcher's list. One group per deployment for the prototype."""
    order = {"admin": 0, "approver": 1}
    people = session.exec(select(Member).where(Member.active).order_by(Member.name)).all()
    return sorted(people, key=lambda m: order.get(m.role, 2))


@router.get("/me", response_model=MemberOut)
def me(member: MemberDep):
    return member
