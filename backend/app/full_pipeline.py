import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auto_apply import run_auto_apply
from app.criteria import load_criteria
from app.full_pipeline_state import FullPipelineRunState
from app.models import Application, ApplicationStatus, Job, JobStatus
from app.pipeline import run_pipeline

logger = logging.getLogger(__name__)


def run_full_pipeline(db: Session, score_threshold: int, state: FullPipelineRunState) -> None:
    """Discover -> score -> promote (score >= score_threshold -> PURSUE) ->
    auto-apply, as one triggered run. Each phase reuses the same function
    the equivalent standalone action calls (run_pipeline, run_auto_apply) —
    this is an orchestration layer, not a reimplementation.

    Phase 3's eligibility check (inside run_auto_apply: JobStatus.PURSUE or
    TAILORED, not yet applied) also picks up any job that was already
    sitting in Pursue/Tailored before this run started, not just the ones
    phase 2 just promoted — same pool "Run Auto-Apply" would touch on its
    own, so this doesn't narrow it to a threshold-only subset.

    Exceptions from either phase (e.g. run_pipeline's FileNotFoundError if
    the base resume is missing) are deliberately NOT caught here — the
    caller (the background-task wrapper in the router) is responsible for
    catching, logging, and calling state.finish(error=...), same split of
    responsibility as pipeline.py's own _run_in_background."""
    state.set_phase("discover_score")
    state.log(f"Phase 1/3: discovering and scoring new jobs (promotion threshold: {score_threshold})…")
    run_pipeline(db, state=state)

    if state.should_stop():
        state.log("Stopped before promotion — no jobs were auto-applied to.")
        return

    state.set_phase("promote")
    state.log(f"Phase 2/3: promoting scored jobs at or above {score_threshold} to Pursue…")
    promoted = _promote_high_scoring_jobs(db, score_threshold)
    state.promoted_count = promoted
    state.log(f"Promoted {promoted} job(s) to Pursue.")

    if state.should_stop():
        state.log("Stopped before auto-apply.")
        return

    criteria = load_criteria()
    if not criteria.auto_apply_enabled:
        state.log("Phase 3/3 skipped: auto_apply_enabled is off in Settings.")
        return
    if not criteria.applicant_profile.is_complete():
        state.log("Phase 3/3 skipped: applicant_profile is incomplete (full_name/email/phone required in Settings).")
        return

    state.set_phase("auto_apply")
    state.log("Phase 3/3: auto-applying to eligible jobs…")

    def on_result(application: Application) -> None:
        if application.status == ApplicationStatus.SUBMITTED:
            state.submitted_count += 1
        elif application.status == ApplicationStatus.FAILED:
            state.failed_count += 1
        else:
            state.unsupported_count += 1

    run_auto_apply(db, criteria, log=state.log, should_stop=state.should_stop, on_result=on_result)


def _promote_high_scoring_jobs(db: Session, score_threshold: int) -> int:
    jobs = db.execute(select(Job).where(Job.status == JobStatus.SCORED)).scalars().all()
    promoted = 0
    for job in jobs:
        latest_score = job.scores[0] if job.scores else None
        if latest_score is not None and latest_score.score >= score_threshold:
            job.status = JobStatus.PURSUE
            promoted += 1
    db.commit()
    return promoted
