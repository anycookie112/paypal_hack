"""What the group sees (docs/SPEC.md §9): "Priya is getting Gloves", "Priya got Gloves, $18.40", with 👍 under them.

Posts are written by code (requests.py, claims.py), never by the AI. Under a "got it" post, anyone but the buyer can
say something's wrong while the money is still in its hold: first by asking the buyer (payouts.ask_buyer), then, if
that doesn't sort it, with a formal report that goes privately to the treasurer (payouts.report_problem). Reports
never appear in the feed.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.decisions import expire_stale
from app.core.requests import ActionError
from app.models import Claim, FeedPost, Group, Member, Reaction, Request, utcnow

LIKE = "👍"
FLAGGABLE = ("waiting", "held")  # still in its hold: the payout hasn't gone out


@dataclass
class FeedItem:
    post_id: int
    kind: str                # on_it | got_it
    text: str
    member: str
    created_at: datetime
    status: str              # on_it: on_it | claimed | expired | released; got_it: the claim's status
    until: datetime | None   # on_it: when they said they'd have it by
    likes: int
    liked: bool              # by the member looking
    claim_id: int | None     # got_it: the purchase, for "something's wrong?"
    can_flag: bool           # got_it, not their own, and the payout is still in its hold


def feed(session: Session, group: Group, viewer: Member, *, limit: int = 50, now: datetime | None = None
         ) -> list[FeedItem]:
    """The newest posts first."""
    expire_stale(session, group.id, now or utcnow())
    posts = session.exec(select(FeedPost).where(FeedPost.group_id == group.id)
                         .order_by(FeedPost.created_at.desc(), FeedPost.id.desc()).limit(limit)).all()
    likes = dict(session.exec(select(Reaction.post_id, func.count()).where(
        Reaction.post_id.in_([p.id for p in posts]), Reaction.emoji == LIKE).group_by(Reaction.post_id)).all())
    mine = set(session.exec(select(Reaction.post_id).where(Reaction.post_id.in_([p.id for p in posts]),
                                                            Reaction.member_id == viewer.id,
                                                            Reaction.emoji == LIKE)).all())
    out = []
    for post in posts:
        request = session.get(Request, post.request_id)
        claim = session.exec(select(Claim).where(Claim.request_id == request.id, Claim.status != "rejected")
                             .order_by(Claim.id.desc())).first() if post.kind == "got_it" else None
        out.append(FeedItem(
            post.id, post.kind, post.text, session.get(Member, post.member_id).name, post.created_at,
            claim.status if claim else request.status, request.on_it_until if post.kind == "on_it" else None,
            likes.get(post.id, 0), post.id in mine, claim.id if claim else None,
            bool(claim and claim.member_id != viewer.id and claim.status in FLAGGABLE)))
    return out


def like(session: Session, post_id: int, by: Member) -> int:
    """👍, or take it back. Returns how many 👍 the post has now."""
    post = session.get(FeedPost, post_id)
    if post is None or post.group_id != by.group_id:
        raise ActionError(f"No post with id {post_id}.")
    mine = session.exec(select(Reaction).where(Reaction.post_id == post_id, Reaction.member_id == by.id,
                                               Reaction.emoji == LIKE)).first()
    if mine:
        session.delete(mine)
    else:
        session.add(Reaction(post_id=post_id, member_id=by.id, emoji=LIKE))
    session.commit()
    return session.exec(select(func.count()).where(Reaction.post_id == post_id, Reaction.emoji == LIKE)).one()
