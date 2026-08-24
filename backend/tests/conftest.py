import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app import models  # noqa: F401  ensure models are registered on Base.metadata


@pytest.fixture()
def db_session():
    # StaticPool is required here: a plain sqlite:///:memory: engine defaults to
    # SingletonThreadPool, which hands out a *fresh* (empty) in-memory database
    # to any thread other than the one that created it — and FastAPI's
    # TestClient runs requests on a separate thread, so router tests would see
    # "no such table" without this.
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
