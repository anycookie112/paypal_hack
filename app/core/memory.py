"""The group's memory: what the AI (and the web pages) look up, fresh, every time (docs/SPEC.md §14.1).

Three layers:
    facts          who's on what, what was bought when, budgets: read from the tables, never remembered by the AI
    patterns       how long things really last, what's probably running low: computed from the facts
    conversations  what was said, so follow-ups make sense; plus a log of every step the AI took

Everything returns plain dataclasses, so the same answer can go to a tool, an API response or a chat message.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic_core import to_jsonable_python
from sqlmodel import Session, select

from app.core import settings
from app.core.decisions import ACCEPTED_CLAIMS, bought_too_soon, committed_cents, expire_stale, last_purchase
from app.core.requests import thing
from app.models import (AgentLog, Category, Claim, Conversation, Group, Item, Member, Message, Report, Request,
                        utcnow)


# --- facts ----------------------------------------------------------------------------------------

@dataclass
class Purchase:
    item: str
    member: str
    total_cents: int
    when: datetime
    store: str
    status: str  # waiting | held | paid


@dataclass
class OnIt:
    request_id: int
    item: str
    member: str
    until: datetime


@dataclass
class Budget:
    name: str
    budget_cents: int     # 0 = no budget set
    committed_cents: int  # spent this month + confirmed but not bought yet

    @property
    def left_cents(self) -> int | None:
        return self.budget_cents - self.committed_cents if self.budget_cents else None


@dataclass
class GroupStatus:
    overall: Budget
    categories: list[Budget]


@dataclass
class OpenRequest:
    request_id: int
    item: str
    status: str                  # on_it | needs_approval
    cap_cents: int
    created_at: datetime
    until: datetime | None       # on_it: when it expires if not bought


@dataclass
class WaitingClaim:
    claim_id: int
    item: str
    total_cents: int
    status: str                  # review (a PIC is checking it) | waiting (the usual hold) | held (a problem was reported)
    payout_due_at: datetime | None


@dataclass
class MemberStatus:
    name: str
    role: str
    monthly_limit_cents: int
    committed_cents: int
    open_requests: list[OpenRequest] = field(default_factory=list)  # on it, or waiting for an approver
    claims_waiting: list[WaitingClaim] = field(default_factory=list)  # receipts in their hold, not paid yet


@dataclass
class ItemInfo:
    item_id: int
    name: str
    other_names: list[str]
    category: str
    cap_cents: int       # the most paid back for one purchase
    restock_days: int    # how long it usually lasts
    proof: str           # receipt | receipt_photo


@dataclass
class BoardItem:
    item_id: int
    name: str
    other_names: list[str]
    category: str
    cap_cents: int                 # the most paid back for one purchase
    restock_days: int              # how long it usually lasts
    proof: str                     # receipt | receipt_photo
    on_it: list[OnIt]              # who's getting it right now
    last_bought: datetime | None   # None = never bought
    last_bought_by: str
    status: str                    # see ItemBoard


@dataclass
class ItemBoard:
    """Every common item and where it stands: what the AI reads instead of searching (it's short). Each item has one
    status, decided by code with the same rules as the decision engine:

        being_got        someone is getting it right now
        recently_bought  bought too recently to get again without an approver (decisions.bought_too_soon)
        running_low      past its usual restock time, and nobody is getting it
        available        anything else: anyone can get one

    Switched-off items aren't on the board: they can't be asked for."""
    items: list[BoardItem]
    others_on_it: list[OnIt]       # things not on the list that someone is getting (a PIC approved them)

    def with_status(self, status: str) -> list[BoardItem]:
        return [i for i in self.items if i.status == status]


def item_board(session: Session, group: Group, now: datetime | None = None) -> ItemBoard:
    now = now or utcnow()
    getting = who_is_on_it(session, group, now)
    by_item: dict[int, list[OnIt]] = {}
    for request_id, item_id in session.exec(select(Request.id, Request.item_id).where(
            Request.id.in_([o.request_id for o in getting]))):
        by_item.setdefault(item_id, []).append(next(o for o in getting if o.request_id == request_id))
    too_soon_percent = settings.get_all(session, group)["too_soon_percent"]
    items = []
    for item in session.exec(select(Item).where(Item.group_id == group.id, Item.active).order_by(Item.name)).all():
        last = last_purchase(session, item)
        on_it = by_item.get(item.id, [])
        if on_it:
            status = "being_got"
        elif last and bought_too_soon(item, last.submitted_at, now, too_soon_percent):
            status = "recently_bought"
        elif last and last.submitted_at + timedelta(days=item.restock_days) <= now:
            status = "running_low"
        else:
            status = "available"
        items.append(BoardItem(
            item.id, item.name, [a.strip() for a in item.aliases.split(",") if a.strip()],
            session.get(Category, item.category_id).name, item.cap_cents, item.restock_days, item.proof, on_it,
            last.submitted_at if last else None, session.get(Member, last.member_id).name if last else "", status))
    return ItemBoard(items, by_item.get(None, []))


def _words(text: str) -> set[str]:
    return {w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", text.lower())}


def find_items(session: Session, group: Group, query: str = "", *, limit: int = 5) -> list[ItemInfo]:
    """Common items whose name or other names share a word with `query`, best match first; every item when `query`
    is empty. Matching is loose on purpose: the AI picks from these, and anything it isn't sure of is treated as
    not on the list."""
    wanted = _words(query)
    scored = []
    for item in session.exec(select(Item).where(Item.group_id == group.id, Item.active).order_by(Item.name)).all():
        score = len(wanted & _words(f"{item.name} {item.aliases}")) if wanted else 1
        if score:
            scored.append((-score, item.name, item))
    found = [item for *_, item in sorted(scored)][:limit if wanted else None]
    return [ItemInfo(item.id, item.name, [a.strip() for a in item.aliases.split(",") if a.strip()],
                     session.get(Category, item.category_id).name, item.cap_cents, item.restock_days, item.proof)
            for item in found]


def recent_purchases(session: Session, group: Group, *, item: Item | None = None, member: Member | None = None,
                     limit: int = 10) -> list[Purchase]:
    query = select(Claim).where(Claim.group_id == group.id, Claim.status.in_(ACCEPTED_CLAIMS))
    if item is not None:
        query = query.where(Claim.item_id == item.id)
    if member is not None:
        query = query.where(Claim.member_id == member.id)
    claims = session.exec(query.order_by(Claim.submitted_at.desc()).limit(limit)).all()
    return [Purchase(item=thing(session, session.get(Request, c.request_id)),
                     member=session.get(Member, c.member_id).name, total_cents=c.total_cents,
                     when=c.submitted_at, store=c.store, status=c.status) for c in claims]


@dataclass
class Answer:
    asked_by: str
    question: str
    answer: str


@dataclass
class PurchaseDetails:
    """Who was in charge of a purchase, for "I can't find the gloves": the AI says who got them, then asks them."""
    claim_id: int
    item: str
    member: str            # who got it
    when: datetime
    store: str
    total_cents: int
    has_photo: bool        # a photo of where it was put
    status: str            # waiting (in its hold) | held | paying | paid | ...
    payout_due_at: datetime | None
    open_questions: int
    open_reports: int
    answers: list[Answer]  # what the buyer said when asked ("on the shelf by the door")


def purchase_details(session: Session, claim: Claim) -> PurchaseDetails:
    reports = session.exec(select(Report).where(Report.claim_id == claim.id, Report.status == "open")).all()
    answered = session.exec(select(Report).where(Report.claim_id == claim.id, Report.kind == "question",
                                                 Report.status == "answered").order_by(Report.created_at)).all()
    return PurchaseDetails(
        claim_id=claim.id, item=thing(session, session.get(Request, claim.request_id)),
        member=session.get(Member, claim.member_id).name, when=claim.submitted_at, store=claim.store,
        total_cents=claim.total_cents, has_photo=bool(claim.photo_path), status=claim.status,
        payout_due_at=claim.payout_due_at,
        open_questions=sum(r.kind == "question" for r in reports), open_reports=sum(r.kind == "report" for r in reports),
        answers=[Answer(session.get(Member, q.member_id).name, q.text, q.answer) for q in answered],
    )


def latest_purchase(session: Session, item: Item) -> PurchaseDetails | None:
    """The most recent purchase of `item`, or None if it's never been bought."""
    last = last_purchase(session, item)
    return purchase_details(session, last) if last else None


@dataclass
class OpenQuestion:
    question_id: int
    item: str
    asked_by: str
    text: str
    when: datetime


def questions_for(session: Session, member: Member) -> list[OpenQuestion]:
    """Unanswered questions about `member`'s purchases ("Marco is looking for the gloves you got")."""
    rows = session.exec(select(Report, Claim).join(Claim, Report.claim_id == Claim.id).where(
        Claim.member_id == member.id, Report.kind == "question", Report.status == "open")
        .order_by(Report.created_at)).all()
    return [OpenQuestion(r.id, thing(session, session.get(Request, c.request_id)), session.get(Member, r.member_id).name,
                         r.text, r.created_at) for r, c in rows]


def who_is_on_it(session: Session, group: Group, now: datetime | None = None) -> list[OnIt]:
    """Everyone getting something right now (confirmed, not bought or expired yet)."""
    now = now or utcnow()
    expire_stale(session, group.id, now)
    requests = session.exec(select(Request).where(Request.group_id == group.id, Request.status == "on_it",
                                                  Request.on_it_until > now).order_by(Request.created_at)).all()
    return [OnIt(r.id, thing(session, r), session.get(Member, r.member_id).name, r.on_it_until) for r in requests]


def group_status(session: Session, group: Group, now: datetime | None = None) -> GroupStatus:
    now = now or utcnow()
    categories = session.exec(select(Category).where(Category.group_id == group.id).order_by(Category.name)).all()
    return GroupStatus(
        overall=Budget(group.name, group.monthly_budget_cents, committed_cents(session, group, now)),
        categories=[Budget(c.name, c.monthly_budget_cents, committed_cents(session, group, now, category=c))
                    for c in categories],
    )


def member_status(session: Session, member: Member, now: datetime | None = None) -> MemberStatus:
    now = now or utcnow()
    group = session.get(Group, member.group_id)
    expire_stale(session, group.id, now)
    open_requests = session.exec(select(Request).where(Request.member_id == member.id,
                                                       Request.status.in_(("on_it", "needs_approval")))
                                 .order_by(Request.created_at)).all()
    waiting = session.exec(select(Claim).where(Claim.member_id == member.id, Claim.status.in_(("review", "waiting", "held")))
                           .order_by(Claim.submitted_at)).all()
    return MemberStatus(
        name=member.name, role=member.role,
        monthly_limit_cents=settings.get_all(session, group)["member_monthly_limit_cents"],
        committed_cents=committed_cents(session, group, now, member=member),
        open_requests=[OpenRequest(r.id, thing(session, r), r.status, r.cap_cents, r.created_at, r.on_it_until)
                       for r in open_requests],
        claims_waiting=[WaitingClaim(c.id, thing(session, session.get(Request, c.request_id)), c.total_cents, c.status,
                                     c.payout_due_at) for c in waiting],
    )


# --- patterns -------------------------------------------------------------------------------------

@dataclass
class ItemHistory:
    item: str
    restock_days: int               # what the treasurer set
    times_bought: int
    last_bought: datetime | None
    last_buyer: str | None
    observed_days: float | None     # average days between purchases, once there are at least two


def item_history(session: Session, item: Item, *, limit: int = 10) -> ItemHistory:
    claims = session.exec(select(Claim).where(Claim.item_id == item.id, Claim.status.in_(ACCEPTED_CLAIMS))
                          .order_by(Claim.submitted_at.desc()).limit(limit)).all()
    gaps = [(newer.submitted_at - older.submitted_at).total_seconds() / 86400
            for newer, older in zip(claims, claims[1:])]
    last = claims[0] if claims else None
    return ItemHistory(
        item=item.name, restock_days=item.restock_days, times_bought=len(claims),
        last_bought=last.submitted_at if last else None,
        last_buyer=session.get(Member, last.member_id).name if last else None,
        observed_days=round(sum(gaps) / len(gaps), 1) if gaps else None,
    )


def probably_needed(session: Session, group: Group, now: datetime | None = None, *, limit: int = 3) -> list[str]:
    """Common items that have probably run out (bought before, and past their usual restock time) and that nobody is
    getting: "Trash bags are low, though: can you grab those?". Most overdue first. Items never bought aren't
    included: there's no evidence they've run out."""
    now = now or utcnow()
    getting = {o.item for o in who_is_on_it(session, group, now)}
    overdue = []
    for item in session.exec(select(Item).where(Item.group_id == group.id, Item.active)).all():
        if item.name in getting:
            continue
        last = last_purchase(session, item)
        if last and (due := last.submitted_at + timedelta(days=item.restock_days)) <= now:
            overdue.append((due, item.name))
    return [name for _, name in sorted(overdue)[:limit]]


# --- conversations and the agent log --------------------------------------------------------------

def get_conversation(session: Session, member: Member, *, channel: str = "web", external_id: str = "") -> Conversation:
    """The member's conversation on this channel (a chat platform's thread id picks one), started if needed."""
    conversation = session.exec(select(Conversation).where(
        Conversation.member_id == member.id, Conversation.channel == channel,
        Conversation.external_id == external_id).order_by(Conversation.updated_at.desc())).first()
    if conversation is None:
        conversation = Conversation(group_id=member.group_id, member_id=member.id, channel=channel,
                                    external_id=external_id)
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    return conversation


def add_message(session: Session, conversation: Conversation, role: str, text: str,
                cards: list[dict] | None = None) -> Message:
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be user or assistant, not {role!r}")
    message = Message(conversation_id=conversation.id, role=role, text=text, cards_json=json.dumps(cards or []))
    conversation.updated_at = utcnow()
    session.add_all([message, conversation])
    session.commit()
    session.refresh(message)
    return message


def recent_messages(session: Session, conversation: Conversation, *, limit: int = 20) -> list[Message]:
    """The last `limit` messages, oldest first: the context for the next reply."""
    newest = session.exec(select(Message).where(Message.conversation_id == conversation.id)
                          .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)).all()
    return list(reversed(newest))


def log_step(session: Session, *, agent: str, event: str, conversation: Conversation | None = None, name: str = "",
             data=None, prompt_id: str = "") -> AgentLog:
    entry = AgentLog(conversation_id=conversation.id if conversation else None, agent=agent, event=event, name=name,
                     data_json=json.dumps(to_jsonable_python(data)), prompt_id=prompt_id)
    session.add(entry)
    session.commit()
    return entry
