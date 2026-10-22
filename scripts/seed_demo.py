"""Fill the database with the demo group (app/core/demo.py): four people, the starter list, and past purchases.

    uv run python scripts/seed_demo.py           # adds the demo group to data/app.db (or DATABASE_URL)
    uv run python scripts/seed_demo.py --reset   # deletes everything first
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel  # noqa: E402

from app.core.demo import seed  # noqa: E402
from app.db import engine, init_db  # noqa: E402


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if "--reset" in sys.argv:
        SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as s:
        seed(s)
