from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) doesn't actually round-trip timezone info on
    SQLite (it has no native datetime type — SQLAlchemy just stores a string
    and reads back a naive datetime, silently dropping tzinfo). Every
    datetime this app stores is UTC (see models._utcnow), so the naive value
    read back IS UTC — this type re-attaches that tzinfo on the way out,
    which matters a lot: without it, FastAPI/Pydantic serialize the
    timestamp with no offset suffix (e.g. "2026-08-23T02:30:27"), and
    JavaScript's `new Date(...)` treats an offset-less ISO string as LOCAL
    time rather than UTC — so every timestamp in the UI reads several hours
    off from the user's actual local time, silently, with no error anywhere.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (ensure models are registered before create_all)

    Base.metadata.create_all(bind=engine)
    _add_is_remote_column_if_missing()
    _backfill_is_remote_from_existing_location()


def _add_is_remote_column_if_missing() -> None:
    """create_all only creates missing tables, never adds columns to one
    that already exists — this app has no migration tooling (see the
    README's "no migration tooling yet" note), and unlike the "delete the
    DB and let it recreate" advice given there, is_remote must be added
    in place: this app now has real tracked-job data in it, and the whole
    point of this column is to enrich that data, not discard it. SQLite-only,
    matching the rest of this app (no other DATABASE_URL is supported
    anywhere in this codebase)."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)").fetchall()}
        if "is_remote" not in columns:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN is_remote BOOLEAN")


def _backfill_is_remote_from_existing_location() -> None:
    """One-time (per row) cleanup for jobs discovered before the
    is_remote/location split existed: re-parses each row's current location
    text so a stored value like "Remote" or "San Francisco, CA (Remote)"
    becomes is_remote=True plus a clean location. Only touches rows where
    is_remote is still unset, so it's a no-op on every startup after the
    first. Rows with an already-blank location are skipped here — nothing
    to parse — and are instead handled by the location-backfill endpoint
    (app/location_backfill.py), which re-fetches them from their source."""
    from app.location_parse import parse_remote_and_location
    from app.models import Job

    with SessionLocal() as db:
        stale_jobs = (
            db.execute(select(Job).where(Job.is_remote.is_(None)).where(Job.location != ""))
            .scalars()
            .all()
        )
        if not stale_jobs:
            return
        for job in stale_jobs:
            is_remote, cleaned = parse_remote_and_location(job.location)
            job.is_remote = is_remote
            job.location = cleaned
        db.commit()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
