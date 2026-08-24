"""On-demand batch tailoring for every Pursue'd job that doesn't have a
resume yet ("Tailor All" on the Tracking page). auto_apply.py and
apply_agent/orchestrator.py each tailor a single missing resume inline,
right where they need one — see their own _apply_to_one_job/worker_loop."""

import logging
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.models import Job, JobStatus, Resume
from app.resume_service import create_tailored_resume

logger = logging.getLogger(__name__)


def _latest_resume(db: Session, job_id: int) -> Resume | None:
    return db.execute(
        select(Resume).where(Resume.job_id == job_id).order_by(Resume.version.desc()).limit(1)
    ).scalar_one_or_none()


def untailored_pursued_jobs(db: Session) -> list[Job]:
    jobs = db.execute(select(Job).where(Job.status == JobStatus.PURSUE)).scalars().all()
    return [job for job in jobs if _latest_resume(db, job.id) is None]


def tailor_all_pursued(
    db: Session,
    provider: LLMProvider,
    base_resume_text: str,
    log: Callable[[str], None] = lambda msg: None,
    should_stop: Callable[[], bool] = lambda: False,
) -> tuple[int, int]:
    """Tailors every job marked Pursue that doesn't have a resume yet.
    Returns (tailored_count, failed_count)."""
    jobs = untailored_pursued_jobs(db)
    log(f"Tailoring {len(jobs)} pursued job(s) with no resume yet.")

    tailored = 0
    failed = 0
    for job in jobs:
        if should_stop():
            log("Tailor-all stopped by user request.")
            break
        try:
            create_tailored_resume(db, job, provider, base_resume_text)
            tailored += 1
            log(f"Tailored: {job.title} @ {job.company}")
        except Exception as exc:
            logger.exception("Tailoring failed for job %s (%s at %s)", job.id, job.title, job.company)
            failed += 1
            log(f"ERROR tailoring {job.title} @ {job.company}: {exc}")

    return tailored, failed
