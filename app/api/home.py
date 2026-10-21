"""The member's home page, the feed, and saying something's wrong. Rules and money live in app/core."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import MemberDep, SessionDep, group_of
from app.core import feed, memory, notifications, payouts
from app.core.requests import release_request

router = APIRouter()


class Home(BaseModel):
    me: memory.MemberStatus
    paypal_email_set: bool
    questions_for_me: list[memory.OpenQuestion]
    group: memory.GroupStatus
    notifications: list[notifications.NotificationItem]
    unread: int


class ReadIn(BaseModel):
    ids: list[int] | None = None  # None: all of them


class AskIn(BaseModel):
    text: str


class ReportIn(BaseModel):
    text: str      # what's wrong
    checked: str   # what they checked first


class AnswerIn(BaseModel):
    answer: str


class Liked(BaseModel):
    likes: int


@router.get("/home", response_model=Home)
def home(session: SessionDep, member: MemberDep):
    items, unread = notifications.for_member(session, member)
    return Home(me=memory.member_status(session, member), paypal_email_set=bool(member.paypal_email),
                questions_for_me=memory.questions_for(session, member),
                group=memory.group_status(session, group_of(session, member)), notifications=items, unread=unread)


@router.post("/notifications/read")
def read(body: ReadIn, session: SessionDep, member: MemberDep):
    return {"marked": notifications.mark_read(session, member, body.ids)}


@router.post("/requests/{request_id}/release")
def release(request_id: int, session: SessionDep, member: MemberDep):
    release_request(session, request_id, member)
    return {"released": request_id}


@router.get("/feed", response_model=list[feed.FeedItem])
def group_feed(session: SessionDep, member: MemberDep):
    return feed.feed(session, group_of(session, member), member)


@router.post("/feed/{post_id}/like", response_model=Liked)
def like(post_id: int, session: SessionDep, member: MemberDep):
    return Liked(likes=feed.like(session, post_id, member))


@router.post("/claims/{claim_id}/ask")
def ask_buyer(claim_id: int, body: AskIn, session: SessionDep, member: MemberDep):
    """Step 1 of "something's wrong": ask the buyer. Nothing else happens."""
    question = payouts.ask_buyer(session, claim_id, member, body.text)
    return {"question_id": question.id}


@router.post("/claims/{claim_id}/report")
def report(claim_id: int, body: ReportIn, session: SessionDep, member: MemberDep):
    """Step 2: the formal report, privately to the treasurer; the payout is held longer."""
    filed = payouts.report_problem(session, claim_id, member, body.text, checked=body.checked)
    return {"report_id": filed.id}


@router.post("/questions/{question_id}/answer")
def answer(question_id: int, body: AnswerIn, session: SessionDep, member: MemberDep):
    payouts.answer_question(session, question_id, member, body.answer)
    return {"answered": question_id}
