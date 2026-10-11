from sqlmodel import Session, SQLModel, create_engine

from app.config import DATA_DIR, DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def init_db(bind=None):
    """Create empty tables. The app starts with no businesses, products or rules."""
    if DATABASE_URL.startswith("sqlite"):
        DATA_DIR.mkdir(exist_ok=True)
    from app import models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(bind or engine)


def get_session():
    with Session(engine) as session:
        yield session
