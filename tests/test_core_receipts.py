"""The receipt reader's reading, and what code does with it (app/core/receipts.py): the photo it's sent, and the
differences from what the member entered, which go to a PIC."""

import hashlib
import io
import json

import pytest
from PIL import Image

from app.core.receipts import Reading, differences, for_reading, save_reading
from tests.demo import NOW, member
from tests.test_core_claims import TODAY, claim, jpg, ready  # noqa: F401  (ready is a fixture)


def reading(**fields) -> Reading:
    """What the reader saw on a $11.99 trash bags receipt from today, changed by `fields`."""
    seen = {"readable": True, "store": "Riverside Hardware", "date": TODAY.isoformat(), "date_as_printed": None,
            "currency": "USD", "total": "11.99", "lines": [{"text": "TRASH BAGS 30CT", "quantity": 1,
                                                             "amount": "11.99"}],
            "has_the_item": "yes", "unsure": []}
    return Reading.model_validate(seen | fields)


def entered(r: Reading, *, day=TODAY, total=11_99) -> list[str]:
    return differences(r, member="Priya", what="Trash bags", receipt_date=day, total_cents=total, currency="USD")


def test_a_phone_photo_is_sent_small_and_upright():
    big = io.BytesIO()
    Image.new("RGB", (2448, 3264), "white").save(big, "PNG")
    media_type, data = for_reading(big.getvalue(), ".png")
    assert media_type == "image/jpeg" and Image.open(io.BytesIO(data)).size == (1200, 1600)
    assert for_reading(b"%PDF-1.7 ...", ".pdf") == ("application/pdf", b"%PDF-1.7 ...")  # PDFs as they are


def test_a_reading_that_agrees_changes_nothing():
    assert entered(reading()) == []


@pytest.mark.parametrize("fields, kwargs, found", [
    ({"total": "18.40"}, {}, "The receipt reader read a total of $18.40; Priya entered $11.99."),
    ({"date": "2026-10-10"}, {}, "The receipt reader read the date as Sat 10 Oct 2026; Priya entered Sat 17 Oct 2026."),
    ({"currency": "CHF", "total": "54.50"}, {}, "The receipt is in CHF (54.50); the group pays back in USD."),
    ({"has_the_item": "no"}, {}, "The receipt reader found nothing on it that looks like Trash bags."),
    ({"readable": False}, {}, "The receipt reader couldn't read this receipt: check the store, date and total."),
])
def test_where_the_reading_and_the_member_differ(fields, kwargs, found):
    assert entered(reading(**fields), **kwargs) == [found]


def test_what_it_couldnt_read_isnt_a_difference():
    assert entered(reading(total=None, date="not a date", has_the_item="unsure")) == []


def test_handing_in_a_receipt_that_was_read(session, ready):  # noqa: F811
    """The member kept the reader's store and date but typed a different total: a PIC sees both."""
    receipt = jpg(7)
    save_reading(session, member(session, "Priya"), ready.id, hashlib.sha256(receipt.data).hexdigest(),
                 reading(total="9.99"), "receipt@v1")
    result = claim(session, "Priya", ready, receipt=receipt, total=11_99, now=NOW)
    assert (result.outcome, result.reasons) == (
        "review", ["The receipt reader read a total of $9.99; Priya entered $11.99."])
    assert json.loads(result.claim.reading_json)["total"] == "9.99"  # kept for the PIC


def test_a_receipt_nobody_read_is_checked_as_before(session, ready):  # noqa: F811
    result = claim(session, "Priya", ready, receipt=jpg(8))
    assert result.outcome == "accepted" and result.claim.reading_json == "{}"
