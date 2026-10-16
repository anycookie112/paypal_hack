"""Who's calling, and turning app/core's errors into responses.

Sign-in is the "View as" switcher (docs/SPEC.md §13, decision 6): the browser sends the member's id in the
X-Member-Id header. Demo-only: anyone can be anyone. The chat platforms will give real identity later.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.requests import ActionError, NeedsInfo
from app.core.setup import NotAllowed
from app.db import get_session
from app.models import Group, Member

SessionDep = Annotated[Session, Depends(get_session)]


def current_member(session: SessionDep, x_member_id: Annotated[int | None, Header()] = None) -> Member:
    member = session.get(Member, x_member_id) if x_member_id else None
    if member is None or not member.active:
        raise HTTPException(401, "Pick who you are in \"View as\".")
    return member


MemberDep = Annotated[Member, Depends(current_member)]


def group_of(session: Session, member: Member) -> Group:
    return session.get(Group, member.group_id)


def add_error_handlers(app: FastAPI) -> None:
    """An ActionError's message is meant for the member: it goes back as the error's detail."""
    @app.exception_handler(ActionError)
    async def action_error(request: Request, error: ActionError):
        body = {"detail": str(error)}
        if isinstance(error, NeedsInfo):
            body["missing"] = error.missing  # what to fill in, decided by the rules
        return JSONResponse(body, status_code=403 if isinstance(error, NotAllowed) else 400)
