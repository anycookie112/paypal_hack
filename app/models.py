"""Database tables (docs/SPEC.md §14.1).

Times are aware UTC (`utcnow()`); SQLModel stores them in UTC and returns them aware, on SQLite and Postgres.
Money is always integer cents.
"""

import json
from datetime import date, datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Group(SQLModel, table=True):
    """A volunteer group, club or team, with the budget everything draws from."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    currency: str = "USD"
    monthly_budget_cents: int = 0  # 0 = no overall monthly budget
    created_at: datetime = Field(default_factory=utcnow)


class Member(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    name: str
    email: str = ""
    paypal_email: str = ""    # where reimbursements go
    role: str = "member"      # member | approver | admin (admins approve too)
    chat_user_id: str = ""    # Telegram / Discord user id, later
    problems_on_record: int = 0  # confirmed problems with their claims (the treasurer can tighten their limits)
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def can_approve(self) -> bool:
        return self.role in ("approver", "admin")


class Category(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    name: str
    monthly_budget_cents: int = 0  # 0 = no category budget


class Item(SQLModel, table=True):
    """A common item: pre-approved up to its cap."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    category_id: int = Field(foreign_key="category.id", index=True)
    name: str
    aliases: str = ""         # comma-separated other names: "work gloves, disposable gloves"
    cap_cents: int            # most one purchase may cost
    restock_days: int = 14    # how long it usually lasts
    proof: str = "receipt"    # receipt | receipt_photo (receipt + a photo of it where it's kept)
    active: bool = True


class Setting(SQLModel, table=True):
    """One of the group's settings (app/core/settings.py), when changed from its default."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    key: str
    value_json: str

    @property
    def value(self):
        return json.loads(self.value_json)


class Request(SQLModel, table=True):
    """Someone telling the group "I'm getting X". Created for every outcome, declined ones too, for the record.

    Status: on_it (confirmed: they're getting it) | needs_approval | declined | rejected | expired | released
            | claimed
    """
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    item_id: int | None = Field(default=None, foreign_key="item.id", index=True)  # None = not a common item
    description: str = ""     # what they said they'd get, for uncommon items especially
    quantity: int = 1
    estimate_cents: int | None = None      # the most the member said it might cost
    estimate_low_cents: int | None = None  # the least (their range's low end; the same if they gave one amount)
    reason: str = ""
    status: str = Field(index=True)
    reasons_json: str = "[]"  # the decision engine's reasons, written by code
    cap_cents: int = 0        # the most that will be paid back, once confirmed
    decided_by_id: int | None = Field(default=None, foreign_key="member.id")  # None = decided by the rules
    decided_at: datetime | None = None
    hours: int | None = None             # how long the member said they need; None = the group's default
    on_it_until: datetime | None = None  # unclaimed after this → expired
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def reasons(self) -> list[str]:
        return json.loads(self.reasons_json)


class Claim(SQLModel, table=True):
    """A receipt for a confirmed request. An accepted claim (anything but rejected) is a purchase.

    Status: review (a check failed: a PIC looks at it first) | waiting (the hold) | held (a problem was reported; the
            hold is extended) | paying (sent to PayPal) | paid | unclaimed (no PayPal account at that email yet) |
            payout_failed | rejected
    """
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    request_id: int = Field(foreign_key="request.id", index=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    item_id: int | None = Field(default=None, foreign_key="item.id", index=True)  # copied from the request
    receipt_path: str = ""
    receipt_hash: str = Field(default="", index=True)
    photo_path: str = ""
    store: str = ""
    receipt_date: date | None = None
    total_cents: int = 0
    base_cents: int = 0       # what's paid after the hold: the total, up to the request's budget
    extra_cents: int = 0      # over the budget: paid later, only if the PIC approves
    extra_reason: str = ""    # the member's justification for the extra
    extra_status: str = "none"  # none | pending | approved | paying | paid | rejected
    reading_json: str = "{}"  # the receipt reader's output, if it ran
    reasons_json: str = "[]"  # review: why a PIC has to look at it, written by code
    status: str = Field(default="waiting", index=True)
    submitted_at: datetime = Field(default_factory=utcnow)
    payout_due_at: datetime | None = None

    @property
    def reasons(self) -> list[str]:
        return json.loads(self.reasons_json)


class Payout(SQLModel, table=True):
    """A PayPal payout for a claim. `sender_batch_id` comes from the claim ("claim-12-1"), so a retry can never pay
    twice: PayPal refuses a repeated id. A new attempt (after an unclaimed payout is cancelled) gets a new id."""
    id: int | None = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    kind: str = "base"        # base (up to the budget) | extra (the approved over-budget part)
    attempt: int = 1
    sender_batch_id: str = Field(index=True, unique=True)
    paypal_batch_id: str = ""
    paypal_item_id: str = ""
    amount_cents: int = 0
    fee_cents: int = 0
    status: str = "CREATED"   # CREATED (not sent yet), then PayPal's item status: PENDING, SUCCESS, UNCLAIMED, ...
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LedgerEntry(SQLModel, table=True):
    """Every movement of the group's money. The balance is the sum of `amount_cents`."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    kind: str                 # top_up | payout | payout_fee | payout_returned
    amount_cents: int         # + money in, - money out
    claim_id: int | None = Field(default=None, foreign_key="claim.id")
    payout_id: int | None = Field(default=None, foreign_key="payout.id")
    paypal_ref: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class FeedPost(SQLModel, table=True):
    """What the group sees: "Priya is getting Gloves" (on_it), "Priya got Gloves" (got_it). Written by code."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    request_id: int = Field(foreign_key="request.id", index=True)
    member_id: int = Field(foreign_key="member.id")
    kind: str                 # on_it | got_it
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class Reaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="feedpost.id", index=True)
    member_id: int = Field(foreign_key="member.id")
    emoji: str = "👍"


class Report(SQLModel, table=True):
    """"Something's wrong" with a purchase, in two steps (docs/SPEC.md §4.1):

    question   "I can't find the gloves": asked of the person who bought them. No hold, nothing to the treasurer.
               Status: open → answered.
    report     the formal report, saying what was checked: the hold is extended and it goes privately to the
               treasurer, never into the feed. Status: open → resolved.
    """
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    member_id: int = Field(foreign_key="member.id")  # who asked or reported
    kind: str = "report"      # question | report
    text: str
    checked: str = ""         # report: what they checked first ("the supply box, asked Priya")
    answer: str = ""          # question: the buyer's answer
    answered_at: datetime | None = None
    status: str = "open"      # open | answered | resolved
    resolution: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    """One chat with the AI: a web chat tab, or (later) a Telegram / Discord DM or thread."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    channel: str = "web"      # web | telegram | discord
    external_id: str = Field(default="", index=True)  # the chat platform's thread id
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    """What was said, so follow-ups make sense. Facts are never taken from here: the AI looks them up fresh."""
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    role: str                 # user | assistant
    text: str
    cards_json: str = "[]"    # decision cards shown with an assistant reply, built by code
    created_at: datetime = Field(default_factory=utcnow)


class StateChange(SQLModel, table=True):
    """A request or claim moving from one status to another (app/core/states.py): what changed since the AI last
    replied, and how a purchase got where it is."""
    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    kind: str                 # request | claim
    record_id: int = Field(index=True)
    from_status: str          # "" = it was just made
    to_status: str
    member_id: int = Field(foreign_key="member.id")  # whose request or claim it is
    by_id: int | None = Field(default=None, foreign_key="member.id")  # who moved it; None = the app (rules, time, PayPal)
    at: datetime = Field(default_factory=utcnow, index=True)


class AgentLog(SQLModel, table=True):
    """Every step the AI took: why the bot said what it said (docs/SPEC.md §14.2)."""
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int | None = Field(default=None, foreign_key="conversation.id", index=True)
    agent: str
    prompt_id: str = ""       # e.g. "group_desk@v1"
    event: str                # tool_call | tool_result | output | error
    name: str = ""            # tool name
    data_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    """Something to tell one member. The web app lists them; the chat bot will send them as messages."""
    id: int | None = Field(default=None, primary_key=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    kind: str                 # needs_approval | approved | rejected | ...
    text: str
    payload_json: str = "{}"
    read_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
