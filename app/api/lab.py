"""The testing grounds (the Lab page): every member's chat side by side, with the AI's log and where things stand.

    GET  /api/lab/log?after=<id>   the agent log for the caller's group: every model call, tool call and result
    GET  /api/lab/status           open requests, receipts on their way to a payout, and the latest moves
    POST /api/lab/reset            delete everything and seed the demo group again (LAB_RESET in .env)

The log shows every member's conversation, including the tools' insides that the chat page never shows: it's for
testing and demos, like "View as".
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select

from app.api.deps import MemberDep, SessionDep
from app.config import LAB_RESET
from app.core import states
from app.core.demo import seed
from app.core.requests import thing
from app.db import engine
from app.models import AgentLog, Claim, Conversation, Member, Request

router = APIRouter(prefix="/lab")

LIVE_CLAIMS = ("review", "waiting", "held", "paying", "unclaimed", "payout_failed")


class LogEntry(BaseModel):
    id: int
    at: datetime
    member: str          # whose conversation
    agent: str           # coordinator | lookup | act
    prompt_id: str       # e.g. "coordinator@v8"
    event: str           # model | tool_call | tool_result | rejected | output | error
    name: str            # the tool, or the model
    data: Any


class OpenRequest(BaseModel):
    id: int
    member: str
    item: str
    status: str          # on_it | needs_approval
    until: datetime | None
    cap_cents: int


class LiveClaim(BaseModel):
    id: int
    member: str
    item: str
    status: str
    total_cents: int
    payout_due_at: datetime | None


class Move(BaseModel):
    at: datetime
    text: str            # "07:28 UTC Priya's Gloves (request 6): getting it → bought (they handed in the receipt)"


class Status(BaseModel):
    requests: list[OpenRequest]
    claims: list[LiveClaim]
    moves: list[Move]    # newest first


@router.get("/log", response_model=list[LogEntry])
def log(session: SessionDep, member: MemberDep, after: int = 0, limit: int = 500):
    rows = session.exec(select(AgentLog, Conversation).join(Conversation, AgentLog.conversation_id == Conversation.id)
                        .where(Conversation.group_id == member.group_id, AgentLog.id > after)
                        .order_by(AgentLog.id).limit(min(limit, 1000))).all()
    names = {m.id: m.name for m in session.exec(select(Member).where(Member.group_id == member.group_id))}
    return [LogEntry(id=entry.id, at=entry.created_at, member=names.get(chat.member_id, "?"), agent=entry.agent,
                     prompt_id=entry.prompt_id, event=entry.event, name=entry.name, data=json.loads(entry.data_json))
            for entry, chat in rows]


@router.get("/status", response_model=Status)
def status(session: SessionDep, member: MemberDep):
    group_id = member.group_id
    names = {m.id: m.name for m in session.exec(select(Member).where(Member.group_id == group_id))}
    requests = session.exec(select(Request).where(Request.group_id == group_id,
                                                  Request.status.in_(("on_it", "needs_approval")))
                            .order_by(Request.created_at)).all()
    claims = session.exec(select(Claim).where(Claim.group_id == group_id, Claim.status.in_(LIVE_CLAIMS))
                          .order_by(Claim.submitted_at)).all()
    return Status(
        requests=[OpenRequest(id=r.id, member=names[r.member_id], item=thing(session, r), status=r.status,
                              until=r.on_it_until, cap_cents=r.cap_cents) for r in requests],
        claims=[LiveClaim(id=c.id, member=names[c.member_id], item=thing(session, session.get(Request, c.request_id)),
                          status=c.status, total_cents=c.total_cents, payout_due_at=c.payout_due_at) for c in claims],
        moves=[Move(at=m.at, text=str(m)) for m in states.recent_moves(session, group_id)])


@router.post("/reset")
def reset(member: MemberDep):
    """Start the demo over: every table emptied and the demo group seeded again (the same people and ids)."""
    if not LAB_RESET:
        raise HTTPException(403, "Resetting is switched off on this server (LAB_RESET=0).")
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    return {"reset": True}
