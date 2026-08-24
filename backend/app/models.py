import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    EXCLUDED = "excluded"
    PURSUE = "pursue"
    SKIP = "skip"
    SNOOZED = "snoozed"
    TAILORED = "tailored"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"


# Statuses that represent an active decision to pursue a job — the Tracking
# Table shows exactly these, per the spec's "flat, sortable/filterable table
# of pursue'd jobs" (company/title/score/links/status/notes).
TRACKING_STATUSES = (
    JobStatus.PURSUE,
    JobStatus.TAILORED,
    JobStatus.APPLIED,
    JobStatus.INTERVIEW,
    JobStatus.REJECTED,
    JobStatus.OFFER,
)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(String(1000))
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200))
    # Geography only (e.g. "San Francisco, CA") — remote-ness lives in
    # is_remote instead of being smashed into this string. See
    # app/location_parse.py, applied at discovery time and, for rows from
    # before that split existed, by the one-time migration in app/db.py.
    location: Mapped[str] = mapped_column(String(200), default="")
    # True/False when the source said either way, None when there was no
    # signal at all (e.g. an empty location string with nothing to parse).
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    salary_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    posted_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.DISCOVERED
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    last_updated: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, onupdate=_utcnow)

    scores: Mapped[list["Score"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Score.scored_at.desc()"
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    score: Mapped[int] = mapped_column(Integer)
    reasoning: Mapped[str] = mapped_column(Text)
    matched_keywords: Mapped[list] = mapped_column(JSON, default=list)
    missing_requirements: Mapped[list] = mapped_column(JSON, default=list)
    role_category: Mapped[str] = mapped_column(String(50))
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    work_authorization: Mapped[dict] = mapped_column(JSON, default=dict)
    scored_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    model_used: Mapped[str] = mapped_column(String(100))
    criteria_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("criteria_config_versions.id"), nullable=True
    )

    job: Mapped["Job"] = relationship(back_populates="scores")


class CriteriaConfigVersion(Base):
    __tablename__ = "criteria_config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    yaml_blob: Mapped[str] = mapped_column(Text)


class Resume(Base):
    """A tailored resume version for a specific job. The base resume itself
    is not stored here — it stays as the file on disk (backend/resume/) per
    the existing design; this table only holds per-job tailored output."""

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    version: Mapped[int] = mapped_column(Integer)
    pdf_path: Mapped[str] = mapped_column(String(500))
    ats_text_path: Mapped[str] = mapped_column(String(500))
    diff_summary: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    job: Mapped["Job"] = relationship()


class ApplicationStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"  # detected but couldn't confidently fill/submit — needs manual completion


class ApplicationMethod(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    AGENT = "agent"  # submitted via the AI agent-apply pipeline (app/apply_agent/)


class Application(Base):
    """One attempt (successful or not) to submit an application for a job.
    A job can have multiple rows here across retries; the Auto-Apply Log
    shows all of them, newest first."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus, native_enum=False, length=20))
    method: Mapped[ApplicationMethod] = mapped_column(Enum(ApplicationMethod, native_enum=False, length=10))
    notes: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    job: Mapped["Job"] = relationship()
