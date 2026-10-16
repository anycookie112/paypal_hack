import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database before it's imported.
_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["LLM_API_KEY"] = "test-key"
os.environ["UPLOADS_DIR"] = str(_tmp / "uploads")
os.environ["PAYOUT_INTERVAL_SECONDS"] = "0"  # never pay anything from a test

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    SQLModel.metadata.drop_all(engine)
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session():
    """A fresh in-memory database per test, for app/core."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, create_engine

    from app import models  # noqa: F401  (register tables)

    memory = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(memory)
    with Session(memory) as s:
        yield s


@pytest.fixture
def group(session):
    """The demo group (tests/demo.py) in the in-memory database."""
    from tests.demo import make_group
    return make_group(session)
