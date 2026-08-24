import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.apply.base import ApplicationAdapter
from app.apply.greenhouse import GreenhouseApplicationAdapter
from app.apply.lever import LeverApplicationAdapter
from app.capsolver import CapSolverClient
from app.config import settings
from app.google_sheets import push_applied_job
from app.llm.base import LLMProvider
from app.llm.factory import make_default_provider
from app.models import Application, ApplicationMethod, ApplicationStatus, Job, JobStatus, Resume
from app.resume import load_base_resume_text
from app.resume_service import create_tailored_resume
from app.schemas import CriteriaConfig

logger = logging.getLogger(__name__)

# Basic anti-bot hygiene per the spec — randomized pause between submissions,
# not a fixed interval.
MIN_DELAY_SECONDS = 8
MAX_DELAY_SECONDS = 20

# Job statuses eligible for auto-apply — must have been actively pursued.
ELIGIBLE_STATUSES = (JobStatus.PURSUE, JobStatus.TAILORED)


def default_adapters() -> list[ApplicationAdapter]:
    return [GreenhouseApplicationAdapter(), LeverApplicationAdapter()]


def _find_adapter(url: str, adapters: list[ApplicationAdapter]) -> ApplicationAdapter | None:
    for adapter in adapters:
        if adapter.supports(url):
            return adapter
    return None


def _latest_resume(db: Session, job_id: int) -> Resume | None:
    return db.execute(
        select(Resume).where(Resume.job_id == job_id).order_by(Resume.version.desc()).limit(1)
    ).scalar_one_or_none()


def _already_applied(db: Session, job_id: int) -> bool:
    existing = db.execute(
        select(Application).where(Application.job_id == job_id, Application.status == ApplicationStatus.SUBMITTED)
    ).scalar_one_or_none()
    return existing is not None


def run_auto_apply(
    db: Session,
    criteria: CriteriaConfig,
    log=lambda msg: None,
    should_stop=lambda: False,
    on_result=lambda application: None,
) -> list[Application]:
    """Synchronous — meant to be called from an explicit, separate trigger
    (never from the regular pipeline run or the GPU-window auto-scheduler),
    on top of the auto_apply_enabled toggle already being on. That double
    gate is deliberate given the stakes of actually submitting applications."""
    if not criteria.auto_apply_enabled:
        raise RuntimeError("auto_apply_enabled is off — turn it on in Settings before running this.")
    if not criteria.applicant_profile.is_complete():
        raise RuntimeError("applicant_profile is incomplete (full_name/email/phone required) — fill it in in Settings.")

    capsolver = CapSolverClient(settings.capsolver_api_key) if settings.capsolver_api_key else None
    adapters = default_adapters()

    # Loaded once up front so a pursued job with no resume yet gets tailored
    # on the fly below instead of being skipped as unsupported. Missing base
    # resume isn't fatal to the whole run — only jobs that actually need
    # tailoring are affected, everything already tailored still applies.
    try:
        base_resume_text: str | None = load_base_resume_text()
        tailor_provider: LLMProvider | None = make_default_provider()
    except FileNotFoundError:
        base_resume_text = None
        tailor_provider = None

    jobs = db.execute(select(Job).where(Job.status.in_(ELIGIBLE_STATUSES))).scalars().all()
    jobs = [j for j in jobs if not _already_applied(db, j.id)]

    if not jobs:
        log("No pursued jobs are eligible for auto-apply (already applied, or none pursued/tailored).")
        return []

    log(f"Auto-apply: {len(jobs)} eligible job(s).")
    results: list[Application] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for i, job in enumerate(jobs):
                if should_stop():
                    log("Auto-apply stopped by user request.")
                    break

                if i > 0:
                    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                    time.sleep(delay)

                application = _apply_to_one_job(
                    db, job, browser, adapters, capsolver, criteria, log, tailor_provider, base_resume_text,
                )
                results.append(application)
                on_result(application)
        finally:
            browser.close()

    return results


def _apply_to_one_job(
    db: Session,
    job: Job,
    browser,
    adapters: list[ApplicationAdapter],
    capsolver: CapSolverClient | None,
    criteria: CriteriaConfig,
    log,
    tailor_provider: LLMProvider | None,
    base_resume_text: str | None,
) -> Application:
    resume = _latest_resume(db, job.id)
    if resume is None:
        if tailor_provider is None:
            log(f"Skipping {job.title} @ {job.company}: no tailored resume yet (and no base resume configured to auto-tailor one).")
            return _record(db, job, None, ApplicationStatus.UNSUPPORTED, "No tailored resume — tailor one first.", criteria)
        log(f"Tailoring resume for {job.title} @ {job.company}…")
        try:
            resume = create_tailored_resume(db, job, tailor_provider, base_resume_text)
        except Exception as exc:
            logger.exception("Auto-tailoring failed for job %s", job.id)
            log(f"Could not tailor resume for {job.title} @ {job.company}: {exc}")
            return _record(db, job, None, ApplicationStatus.UNSUPPORTED, f"Resume tailoring failed: {exc}", criteria)

    application_url = job.canonical_url or job.source_url
    adapter = _find_adapter(application_url, adapters)
    if adapter is None:
        log(f"Skipping {job.title} @ {job.company}: no adapter for this platform (source: {job.source}).")
        return _record(
            db, job, resume, ApplicationStatus.UNSUPPORTED,
            f"No auto-apply adapter for source '{job.source}' — apply manually via the Tracking table.",
            criteria,
        )

    log(f"Applying: {job.title} @ {job.company} via {adapter.name}…")
    page = browser.new_page()
    try:
        result = adapter.submit(
            page=page,
            application_url=application_url,
            profile=criteria.applicant_profile,
            resume_pdf_path=Path(resume.pdf_path),
            screenshot_dir=settings.application_screenshot_dir / str(job.id),
            capsolver=capsolver,
        )
    except Exception as exc:
        logger.exception("Auto-apply crashed for job %s", job.id)
        log(f"ERROR applying to {job.title} @ {job.company}: {exc}")
        return _record(db, job, resume, ApplicationStatus.FAILED, f"Unexpected error: {exc}", criteria)
    finally:
        page.close()

    status = {
        "submitted": ApplicationStatus.SUBMITTED,
        "failed": ApplicationStatus.FAILED,
        "unsupported": ApplicationStatus.UNSUPPORTED,
    }[result.status]
    log(f"{'Submitted' if status == ApplicationStatus.SUBMITTED else status.value.capitalize()}: {job.title} @ {job.company} — {result.notes}")
    return _record(db, job, resume, status, result.notes, criteria, screenshot_path=result.screenshot_path)


def _record(
    db: Session,
    job: Job,
    resume: Resume | None,
    status: ApplicationStatus,
    notes: str,
    criteria: CriteriaConfig,
    screenshot_path: str | None = None,
) -> Application:
    application = Application(
        job_id=job.id,
        resume_id=resume.id if resume else None,
        status=status,
        method=ApplicationMethod.AUTO,
        notes=notes,
        screenshot_path=screenshot_path,
        submitted_at=datetime.now(timezone.utc) if status == ApplicationStatus.SUBMITTED else None,
    )
    db.add(application)
    if status == ApplicationStatus.SUBMITTED:
        job.status = JobStatus.APPLIED
    db.commit()
    db.refresh(application)
    if status == ApplicationStatus.SUBMITTED:
        push_applied_job(job, criteria, method_label="Auto-apply")
    return application
