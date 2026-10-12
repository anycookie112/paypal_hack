import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database before it's imported.
_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["LLM_API_KEY"] = "test-key"

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
