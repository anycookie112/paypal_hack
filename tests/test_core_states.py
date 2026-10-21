"""How requests and claims move (app/core/states.py): the table, the log, and who sees which moves."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.claims import Upload, submit_claim
from app.core.decisions import expire_stale
from app.core.payouts import report_problem
from app.core.requests import approve_request, release_request
from app.core.states import CLAIM_STATES, REQUEST_STATES, lifecycle, move, moves_since
from app.models import StateChange
from tests.demo import NOW, member, tell


def log(session):
    return [(c.kind, c.record_id, c.from_status, c.to_status) for c in
            session.exec(select(StateChange).order_by(StateChange.id))]


def bought(session, who, request, total=11_99):
    buyer = member(session, who)
    buyer.paypal_email = f"{who.lower()}@paypal.example.com"
    return submit_claim(session, buyer, request.id, receipt=Upload(b"\xff\xd8\xff\xe0 r", "r.jpg"),
                        store="Riverside Hardware", receipt_date=NOW.date(), total_cents=total,
                        photo=Upload(b"\xff\xd8\xff\xe0 p", "p.jpg"), now=NOW + timedelta(minutes=5)).claim


def test_every_move_is_logged(session, group):
    gloves = tell(session, "Priya", "Gloves").request
    table = tell(session, "Marco", description="Folding table", estimate_cents=40_00).request
    approve_request(session, table.id, member(session, "Dana"), now=NOW)
    claim = bought(session, "Priya", gloves)
    bags = tell(session, "Marco", "Trash bags").request
    expire_stale(session, group.id, NOW + timedelta(days=1))
    assert log(session) == [
        ("request", gloves.id, "", "on_it"),
        ("request", table.id, "", "needs_approval"),
        ("request", table.id, "needs_approval", "on_it"),
        ("request", gloves.id, "on_it", "claimed"),
        ("claim", claim.id, "", "waiting"),
        ("request", bags.id, "", "on_it"),
        ("request", table.id, "on_it", "expired"),
        ("request", bags.id, "on_it", "expired"),
    ]
    approved = session.exec(select(StateChange).where(StateChange.to_status == "on_it",
                                                      StateChange.record_id == table.id)).one()
    assert (approved.member_id, approved.by_id) == (member(session, "Marco").id, member(session, "Dana").id)


def test_a_move_the_table_doesnt_have_is_a_bug(session, group):
    request = tell(session, "Priya", "Gloves").request
    release_request(session, request.id, member(session, "Priya"))
    with pytest.raises(ValueError, match="A request can't go from released to claimed"):
        move(session, request, "claimed")
    move(session, request, "released")  # where it already is: nothing to do
    assert [to for *_, to in log(session)] == ["on_it", "released"]


def test_what_moved_since_and_who_sees_it(session, group):
    gloves = tell(session, "Priya", "Gloves").request
    claim = bought(session, "Priya", gloves)
    report_problem(session, claim.id, member(session, "Marco"), "Not in the box", checked="The box",
                   now=NOW + timedelta(minutes=10))
    since = NOW + timedelta(minutes=1)

    marco = [str(m) for m in moves_since(session, member(session, "Marco"), since)]
    assert marco == [f"08:45 UTC Priya's Gloves (request {gloves.id}): getting it → bought (they handed in the "
                     "receipt)"]  # someone else's receipt and its report aren't his to see
    priya = [str(m) for m in moves_since(session, member(session, "Priya"), since)]
    assert priya[1:] == [
        f"08:45 UTC Priya's receipt for Gloves (claim {claim.id}): new → in its hold before the payout (the receipt "
        "passed every check)",
        f"08:50 UTC Priya's receipt for Gloves (claim {claim.id}): in its hold before the payout → on hold for longer "
        "(someone reported a problem)"]
    assert len(moves_since(session, member(session, "Dana"), since)) == 3  # a PIC sees receipts too
    assert moves_since(session, member(session, "Priya"), NOW + timedelta(minutes=10)) == []


def test_the_map_covers_every_status(session):
    text = lifecycle()
    for status in [*REQUEST_STATES, *CLAIM_STATES]:
        assert f"\n- {status}: " in text
    assert "- on_it: getting it. Next: bought (claimed) when they handed in the receipt;" in text
