"""The receipt reader (app/ai/receipts.py): one model call with the receipt, fixed fields back (fake model)."""

import asyncio
import io

import pytest
from PIL import Image

from app.ai.model import AgentError
from app.ai.receipts import read_receipt
from tests.fakes import FakeChatModel, call

READ = {"readable": True, "store": "Riverside Hardware", "date": "2026-10-17", "date_as_printed": "10/17/26",
        "currency": "USD", "total": "18.40", "lines": [{"text": "WORK GLOVES", "quantity": 2, "amount": "16.00"}],
        "has_the_item": "yes", "unsure": []}


def photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (3000, 4000), "white").save(out, "JPEG")
    return out.getvalue()


def test_one_call_with_the_receipt_and_what_they_bought():
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages) or call("Reading", READ))
    reading, how = asyncio.run(read_receipt(photo(), ".jpg", what="Gloves", model=model))
    assert (reading.total_cents(), reading.day().isoformat(), reading.has_the_item) == (18_40, "2026-10-17", "yes")
    assert how["prompt_id"] == "receipt@v1" and isinstance(how["ms"], int)
    [messages] = seen
    assert messages[0].content.startswith("<role>\nYou read one receipt for Group Fund")
    image, text = messages[1].content
    assert (image["type"], image["mime_type"]) == ("image", "image/jpeg")  # the phone photo, made small
    assert text == {"type": "text", "text": "The member says they bought: Gloves. Read this receipt."}


def test_a_pdf_goes_as_a_file():
    seen = []
    model = FakeChatModel(respond=lambda messages, tools: seen.append(messages) or call("Reading", READ))
    asyncio.run(read_receipt(b"%PDF-1.7 a receipt", ".pdf", what="Gloves", model=model))
    assert (seen[0][1].content[0]["type"], seen[0][1].content[0]["mime_type"]) == ("file", "application/pdf")


@pytest.mark.parametrize("respond", [
    lambda messages, tools: (_ for _ in ()).throw(TimeoutError()),        # the provider failed
    lambda messages, tools: call("Reading", {"readable": True}),          # fields missing
])
def test_when_it_cant_read_the_member_types_the_values(respond):
    with pytest.raises(AgentError, match="^receipt:"):
        asyncio.run(read_receipt(photo(), ".jpg", what="Gloves", model=FakeChatModel(respond=respond)))
