"""Receipts read by the AI (app/ai/receipts.py), and what code does with a reading.

The reader only reads. Its reading fills in the claim form; when the claim is handed in, code compares what the member
entered with what the reader saw, and any difference goes to a PIC with both values (app/core/claims.py). Nothing is
refused because of a reading, and code never takes the reading over the member's word: it only says where they differ.
"""

import datetime as dt  # not `date`: Reading has a field called date
import io
import json
from typing import Literal

import pillow_heif
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.domain.money import cents_to_str, to_cents
from app.models import Member, ReceiptRead

READ_LONGEST_PX = 1600  # enough to read a receipt; a phone photo can be 11 MB, over the model's 5 MB per image

pillow_heif.register_heif_opener()  # iPhone photos (HEIC): the model doesn't take them, so they're converted


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="The line as printed.")
    quantity: int | None = Field(description="How many, if the line says.")
    amount: str | None = Field(description="The line's amount as printed, without the currency.")


class Reading(BaseModel):
    """What the reader saw on one receipt. Amounts and dates are text as the model wrote them: code parses them."""
    model_config = ConfigDict(extra="forbid")
    readable: bool = Field(description="False if it isn't a receipt, or the total can't be read.")
    store: str | None = Field(description="The business name as printed.")
    date: str | None = Field(description="The purchase date as YYYY-MM-DD.")
    date_as_printed: str | None
    currency: str | None = Field(description="The ISO 4217 code of the total's currency.")
    total: str | None = Field(description="The amount charged, in that currency, without the currency.")
    lines: list[Line]
    has_the_item: Literal["yes", "no", "unsure"] = Field(
        description="Whether a line is what the member says they bought.")
    unsure: list[str] = Field(description="Anything hard to read or ambiguous, a few words each.")

    def total_cents(self) -> int | None:
        try:
            return to_cents(self.total) if self.total else None
        except ValueError:
            return None

    def day(self) -> dt.date | None:
        try:
            return dt.date.fromisoformat(self.date) if self.date else None
        except ValueError:
            return None


def for_reading(data: bytes, kind: str) -> tuple[str, bytes]:
    """(media type, bytes) to send to the reader: a PDF as it is; a photo turned upright and made a JPEG at most
    READ_LONGEST_PX on its longest side. `kind` is the file's real type (claims.file_kind)."""
    if kind == ".pdf":
        return "application/pdf", data
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((READ_LONGEST_PX, READ_LONGEST_PX))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return "image/jpeg", out.getvalue()


def save_reading(session: Session, member: Member, request_id: int, receipt_hash: str, reading: Reading,
                 prompt_id: str) -> ReceiptRead:
    row = ReceiptRead(group_id=member.group_id, member_id=member.id, request_id=request_id,
                      receipt_hash=receipt_hash, reading_json=reading.model_dump_json(), prompt_id=prompt_id)
    session.add(row)
    session.commit()
    return row


def reading_for(session: Session, group_id: int, receipt_hash: str) -> Reading | None:
    """The latest reading of this exact file in the group, if the reader ran on it."""
    row = session.exec(select(ReceiptRead).where(ReceiptRead.group_id == group_id,
                                                 ReceiptRead.receipt_hash == receipt_hash)
                       .order_by(ReceiptRead.id.desc())).first()
    return Reading.model_validate(json.loads(row.reading_json)) if row else None


def differences(reading: Reading, *, member: str, what: str, receipt_date: dt.date, total_cents: int,
                currency: str) -> list[str]:
    """Where the reading and what `member` entered disagree, for a PIC: empty if they agree."""
    if not reading.readable:
        return ["The receipt reader couldn't read this receipt: check the store, date and total."]
    out = []
    read_total = reading.total_cents()
    if reading.currency and reading.currency.upper() != currency:
        out.append(f"The receipt is in {reading.currency.upper()} ({reading.total or 'total unread'}); "
                   f"the group pays back in {currency}.")
    elif read_total is not None and read_total != total_cents:
        out.append(f"The receipt reader read a total of {cents_to_str(read_total)}; {member} entered "
                   f"{cents_to_str(total_cents)}.")
    if (day := reading.day()) and day != receipt_date:
        out.append(f"The receipt reader read the date as {day:%a %d %b %Y}; {member} entered "
                   f"{receipt_date:%a %d %b %Y}.")
    if reading.has_the_item == "no":
        out.append(f"The receipt reader found nothing on it that looks like {what}.")
    return out
