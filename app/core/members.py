"""Members' own details: where their payouts go."""

import re

from sqlmodel import Session

from app.core.requests import ActionError
from app.models import Member

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def set_paypal_email(session: Session, member: Member, email: str) -> Member:
    """Where `member`'s payouts go. Asked for before their first claim (docs/SPEC.md §4.1)."""
    email = email.strip()
    if not _EMAIL.fullmatch(email):
        raise ActionError(f"\"{email}\" doesn't look like an email address.")
    member.paypal_email = email
    session.add(member)
    session.commit()
    session.refresh(member)
    return member
