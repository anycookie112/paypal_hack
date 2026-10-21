"""What one member has been told: approvals, payouts, questions about their purchases (docs/SPEC.md §14, rule 4).

Code records them (requests.notify); the web app lists them, and the chat bot will send them as messages.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Member, Notification, utcnow


@dataclass
class NotificationItem:
    id: int
    kind: str
    text: str
    created_at: datetime
    read: bool
    payload: dict  # ids the page can link to (request_id, claim_id, report_id)


def for_member(session: Session, member: Member, *, limit: int = 20) -> tuple[list[NotificationItem], int]:
    """The newest notifications, and how many are unread."""
    rows = session.exec(select(Notification).where(Notification.member_id == member.id)
                        .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)).all()
    unread = session.exec(select(func.count()).where(Notification.member_id == member.id,
                                                     Notification.read_at.is_(None))).one()
    return [NotificationItem(n.id, n.kind, n.text, n.created_at, n.read_at is not None, json.loads(n.payload_json))
            for n in rows], unread


def mark_read(session: Session, member: Member, ids: list[int] | None = None, *, now: datetime | None = None) -> int:
    """Mark these (or all) of the member's notifications read; only their own. Returns how many changed."""
    query = select(Notification).where(Notification.member_id == member.id, Notification.read_at.is_(None))
    if ids is not None:
        query = query.where(Notification.id.in_(ids))
    rows = session.exec(query).all()
    for n in rows:
        n.read_at = now or utcnow()
        session.add(n)
    session.commit()
    return len(rows)
