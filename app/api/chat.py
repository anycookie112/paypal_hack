"""Chatting with the AI in the web app: the conversation so far, and a reply streamed as it's written.

The stream is newline-delimited JSON, one event per line:

    {"type": "step", "text": "Checking the group's records…"}   the coordinator asked a helper
    {"type": "text", "text": "You're on"}                        a piece of the reply
    {"type": "done", "text": "...", "failed": false}             the whole reply, as saved: show this one

Only the coordinator's reply and plain-words steps reach the browser, never the tools' arguments or results.
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from app import ai
from app.ai.main import stream_reply
from app.api.deps import MemberDep, SessionDep
from app.core import memory
from app.db import engine
from app.models import Member

router = APIRouter()

STEPS = {"lookup": "Checking the group's records…", "act": "Sorting it out…"}
MAX_MESSAGE = 2000


class ChatMessage(BaseModel):
    role: str  # user | assistant
    text: str
    created_at: datetime


class Conversation(BaseModel):
    ai_available: bool
    messages: list[ChatMessage]


class SayIn(BaseModel):
    text: str


@router.get("/chat", response_model=Conversation)
def conversation(session: SessionDep, member: MemberDep):
    chat = memory.get_conversation(session, member)
    return Conversation(ai_available=ai.available(), messages=[
        ChatMessage(role=m.role, text=m.text, created_at=m.created_at)
        for m in memory.recent_messages(session, chat, limit=50)])


@router.post("/chat")
async def say(body: SayIn, member: MemberDep):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Say something first.")
    if len(text) > MAX_MESSAGE:
        raise HTTPException(400, f"Keep it under {MAX_MESSAGE} characters.")
    if not ai.available():
        raise HTTPException(503, "The AI isn't set up on this server yet.")
    member_id = member.id

    async def events():
        # Its own session: the request's closes when this function returns, before the stream is done.
        with Session(engine) as session:
            async for event in stream_reply(session, session.get(Member, member_id), text):
                if event.type == "text":
                    yield _line({"type": "text", "text": event.text})
                elif event.type == "tool_call" and event.name in STEPS:
                    yield _line({"type": "step", "text": STEPS[event.name]})
                elif event.type == "output":
                    yield _line({"type": "done", "text": event.data.text, "failed": event.data.failed})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"
