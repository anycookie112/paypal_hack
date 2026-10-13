"""What every agent run gets: who's chatting. Tools act as this member and read through this session."""

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime

from sqlmodel import Session

from app.domain.money import cents_to_str
from app.models import Group, Member, utcnow


@dataclass
class Chat:
    session: Session
    member: Member
    now: datetime | None = None  # tests pin the clock; None = the real time

    @property
    def group(self) -> Group:
        return self.session.get(Group, self.member.group_id)

    @property
    def time(self) -> datetime:
        return self.now or utcnow()


def when(moment: datetime) -> str:
    return f"{moment:%a %d %b, %H:%M} UTC"


def facts(value) -> str:
    """A tool's result as JSON the model reads easily: money in dollars ("cap": "$25.00"), times in words."""
    def tidy(v):
        if is_dataclass(v):
            names = [f.name for f in fields(v)] + [n for n, a in vars(type(v)).items() if isinstance(a, property)]
            v = {n: getattr(v, n) for n in names}
        if isinstance(v, dict):
            return {k.removesuffix("_cents"): cents_to_str(x) if k.endswith("_cents") and isinstance(x, int)
                    else tidy(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [tidy(x) for x in v]
        if isinstance(v, datetime):
            return when(v)
        return v
    return json.dumps(tidy(value), ensure_ascii=False)
