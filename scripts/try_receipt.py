"""Read one receipt with the app's receipt reader and print the reading: the quickest way to try a receipt.

    uv run python scripts/try_receipt.py path/to/receipt.jpg "Gloves"

Uses the model in .env. Nothing is saved.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.ai.receipts import read_receipt  # noqa: E402
from app.core.claims import Upload, file_kind  # noqa: E402


async def main(path: str, what: str) -> None:
    data = Path(path).read_bytes()
    reading, call = await read_receipt(data, file_kind(Upload(data, Path(path).name)), what=what)
    print(json.dumps(call))
    print(json.dumps(reading.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "something for the group"))
