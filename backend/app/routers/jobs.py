import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.criteria import load_criteria
from app.db import SessionLocal, get_db
from app.google_sheets import push_applied_job
from app.location_backfill import backfill_locations
from app.location_backfill_state import location_backfill_state
from app.models import TRACKING_STATUSES, Job, JobStatus
from app.pipeline_state import RunStatus
from app.schemas import JobDetailOut, JobOut, JobUpdate, LocationBackfillStatusOut, ScoreOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Statuses that have been triaged out of the main review-queue view — they
# either graduated to the Tracking Table (pursue and beyond) or were
# dismissed (skip/snoozed/excluded).
_DEFAULT_VIEW_STATUSES = (JobStatus.DISCOVERED, JobStatus.SCORED)

# Shown on the "Excluded" tab: both the pipeline's own hard-exclusions
# (EXCLUDED) and jobs a user dismissed by hand — either from the Jobs
# listing ("Exclude") or after removing them from the Tracking Table
# ("Remove", which also lands here as SKIP) — so dismissing something
# always has one visible, recoverable home instead of disappearing outright.
_DISMISSED_VIEW_STATUSES = (JobStatus.EXCLUDED, JobStatus.SKIP)


def _build_job_out(job: Job, include_description: bool = False) -> JobOut | JobDetailOut:
    latest = job.scores[0] if job.scores else None
    score_out = (
        ScoreOut(
            score=latest.score,
            reasoning=latest.reasoning,
            matched_keywords=latest.matched_keywords,
            missing_requirements=latest.missing_requirements,
            role_category=latest.role_category,
            red_flags=latest.red_flags,
            work_authorization=latest.work_authorization,
            scored_at=latest.scored_at,
            model_used=latest.model_used,
        )
        if latest
        else None
    )
    kwargs = dict(
        id=job.id,
        source=job.source,
        source_url=job.source_url,
        canonical_url=job.canonical_url,
        title=job.title,
        company=job.company,
        location=job.location,
        is_remote=job.is_remote,
        salary_text=job.salary_text,
        posted_date=job.posted_date,
        first_seen=job.first_seen,
        status=job.status,
        notes=job.notes,
        last_updated=job.last_updated,
        latest_score=score_out,
    )
    if include_description:
        return JobDetailOut(**kwargs, description=job.description)
    return JobOut(**kwargs)


@router.get("/excluded", response_model=list[JobOut])
def list_excluded_jobs(db: Session = Depends(get_db)) -> list[JobOut]:
    jobs = db.execute(select(Job).where(Job.status.in_(_DISMISSED_VIEW_STATUSES))).scalars().all()
    outs = [_build_job_out(j) for j in jobs]
    outs.sort(key=lambda o: o.first_seen, reverse=True)
    return outs


@router.get("/tracking", response_model=list[JobOut])
def list_tracking_jobs(db: Session = Depends(get_db)) -> list[JobOut]:
    jobs = db.execute(select(Job).where(Job.status.in_(TRACKING_STATUSES))).scalars().all()
    outs = [_build_job_out(j) for j in jobs]
    outs.sort(key=lambda o: o.last_updated, reverse=True)
    return outs


@router.get("/backfill-locations/status", response_model=LocationBackfillStatusOut)
def get_backfill_locations_status() -> LocationBackfillStatusOut:
    return LocationBackfillStatusOut(**location_backfill_state.snapshot())


@router.post("/backfill-locations/stop", response_model=LocationBackfillStatusOut)
def stop_backfill_locations() -> LocationBackfillStatusOut:
    location_backfill_state.request_stop()
    return LocationBackfillStatusOut(**location_backfill_state.snapshot())


@router.post("/backfill-locations", response_model=LocationBackfillStatusOut, status_code=202)
def trigger_backfill_locations(background_tasks: BackgroundTasks) -> LocationBackfillStatusOut:
    if location_backfill_state.status != RunStatus.RUNNING:
        location_backfill_state.reset_for_new_run()
        background_tasks.add_task(_run_backfill_locations_in_background)
    return LocationBackfillStatusOut(**location_backfill_state.snapshot())


def _run_backfill_locations_in_background() -> None:
    db = SessionLocal()
    try:
        updated, not_found, skipped = backfill_locations(
            db, log=location_backfill_state.log, should_stop=location_backfill_state.should_stop
        )
        location_backfill_state.updated_count = updated
        location_backfill_state.not_found_count = not_found
        location_backfill_state.skipped_count = skipped
        if location_backfill_state.should_stop():
            location_backfill_state.finish(stopped=True)
        else:
            location_backfill_state.finish()
    except Exception as exc:
        logger.exception("Location backfill failed")
        location_backfill_state.log(f"ERROR: {exc}")
        location_backfill_state.finish(error=str(exc))
    finally:
        db.close()


# NOTE: this and every route below must stay below the static /backfill-*
# routes above — FastAPI/Starlette matches routes in registration order and
# {job_id} (an untyped path param) would otherwise shadow them, since the
# str-to-int coercion only happens at parameter-binding time, not at route
# matching time.
@router.get("", response_model=list[JobOut])
def list_jobs(
    status: JobStatus | None = None,
    role_category: str | None = None,
    source: str | None = None,
    sort: str = Query("score_desc", pattern="^(score_desc|score_asc|first_seen_desc)$"),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    query = select(Job)
    if status is not None:
        query = query.where(Job.status == status)
    else:
        query = query.where(Job.status.in_(_DEFAULT_VIEW_STATUSES))
    if source is not None:
        query = query.where(Job.source == source)

    jobs = db.execute(query).scalars().all()

    if role_category is not None:
        jobs = [j for j in jobs if j.scores and j.scores[0].role_category == role_category]

    outs = [_build_job_out(j) for j in jobs]

    if sort == "score_desc":
        outs.sort(key=lambda o: o.latest_score.score if o.latest_score else -1, reverse=True)
    elif sort == "score_asc":
        outs.sort(key=lambda o: o.latest_score.score if o.latest_score else -1)
    else:
        outs.sort(key=lambda o: o.first_seen, reverse=True)

    return outs


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobDetailOut:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _build_job_out(job, include_description=True)


@router.patch("/{job_id}", response_model=JobDetailOut)
def update_job(job_id: int, update: JobUpdate, db: Session = Depends(get_db)) -> JobDetailOut:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    was_applied = job.status == JobStatus.APPLIED

    if update.status is not None:
        job.status = JobStatus(update.status)
    if update.notes is not None:
        job.notes = update.notes
    if update.company is not None:
        job.company = update.company
    if update.location is not None:
        job.location = update.location
    if "is_remote" in update.model_fields_set:
        job.is_remote = update.is_remote

    just_applied = job.status == JobStatus.APPLIED and not was_applied

    db.commit()
    db.refresh(job)

    if just_applied:
        # Only on the actual pursue -> applied transition, not on every
        # subsequent edit to an already-applied job (e.g. fixing a typo in
        # notes shouldn't push a second row to the sheet).
        push_applied_job(job, load_criteria(), method_label="Manual")

    return _build_job_out(job, include_description=True)
