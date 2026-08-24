import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.auto_apply_state import auto_apply_state
from app.criteria import load_criteria
from app.db import SessionLocal
from app.full_pipeline import run_full_pipeline
from app.full_pipeline_state import full_pipeline_state
from app.pipeline import run_pipeline_in_background, run_rescore_in_background
from app.pipeline_state import RunStatus, pipeline_state
from app.schemas import FullPipelineStatusOut, PipelineStatusOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", response_model=PipelineStatusOut, status_code=202)
def trigger_pipeline_run(background_tasks: BackgroundTasks) -> PipelineStatusOut:
    if pipeline_state.status == RunStatus.RUNNING:
        return PipelineStatusOut(**pipeline_state.snapshot())
    if full_pipeline_state.status == RunStatus.RUNNING:
        raise HTTPException(status_code=400, detail="A Full Pipeline run is already in progress — wait for it to finish first.")
    pipeline_state.reset_for_new_run()
    background_tasks.add_task(run_pipeline_in_background)
    return PipelineStatusOut(**pipeline_state.snapshot())


@router.post("/rescore", response_model=PipelineStatusOut, status_code=202)
def trigger_rescore(
    background_tasks: BackgroundTasks,
    force: bool = False,
    min_score: int | None = None,
    max_score: int | None = None,
) -> PipelineStatusOut:
    if pipeline_state.status != RunStatus.RUNNING:
        pipeline_state.reset_for_new_run()
        background_tasks.add_task(run_rescore_in_background, force=force, min_score=min_score, max_score=max_score)
    return PipelineStatusOut(**pipeline_state.snapshot())


@router.post("/stop", response_model=PipelineStatusOut)
def stop_pipeline_run() -> PipelineStatusOut:
    pipeline_state.request_stop()
    return PipelineStatusOut(**pipeline_state.snapshot())


@router.get("/status", response_model=PipelineStatusOut)
def get_pipeline_status() -> PipelineStatusOut:
    return PipelineStatusOut(**pipeline_state.snapshot())


# ---- Full Pipeline: discover -> score -> promote -> auto-apply ----
#
# A distinct, higher-stakes run — it can end up submitting real applications
# with no per-job review — so it gets its own state object (same reasoning
# as auto_apply_state being separate from pipeline_state) and refuses to
# start while ANY of the three run types is already active, since a plain
# Discover & Score or a plain Run Auto-Apply running concurrently would race
# on the same Job rows.

@router.get("/full-run/status", response_model=FullPipelineStatusOut)
def get_full_pipeline_status() -> FullPipelineStatusOut:
    return FullPipelineStatusOut(**full_pipeline_state.snapshot())


@router.post("/full-run/stop", response_model=FullPipelineStatusOut)
def stop_full_pipeline() -> FullPipelineStatusOut:
    full_pipeline_state.request_stop()
    return FullPipelineStatusOut(**full_pipeline_state.snapshot())


@router.post("/full-run", response_model=FullPipelineStatusOut, status_code=202)
def trigger_full_pipeline(background_tasks: BackgroundTasks, score_threshold: int = 80) -> FullPipelineStatusOut:
    if full_pipeline_state.status == RunStatus.RUNNING:
        return FullPipelineStatusOut(**full_pipeline_state.snapshot())
    if pipeline_state.status == RunStatus.RUNNING:
        raise HTTPException(status_code=400, detail="A Discover & Score run is already in progress — wait for it to finish first.")
    if auto_apply_state.status == RunStatus.RUNNING:
        raise HTTPException(status_code=400, detail="A Run Auto-Apply run is already in progress — wait for it to finish first.")

    try:
        criteria = load_criteria()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not criteria.auto_apply_enabled:
        raise HTTPException(status_code=400, detail="auto_apply_enabled is off — turn it on in Settings first.")
    if not criteria.applicant_profile.is_complete():
        raise HTTPException(
            status_code=400,
            detail="applicant_profile is incomplete (full_name/email/phone required) — fill it in in Settings.",
        )

    full_pipeline_state.reset_for_new_run(score_threshold)
    background_tasks.add_task(_run_full_pipeline_in_background, score_threshold)
    return FullPipelineStatusOut(**full_pipeline_state.snapshot())


def _run_full_pipeline_in_background(score_threshold: int) -> None:
    db = SessionLocal()
    try:
        run_full_pipeline(db, score_threshold, full_pipeline_state)
        if full_pipeline_state.should_stop():
            full_pipeline_state.finish(stopped=True)
        else:
            full_pipeline_state.finish()
    except Exception as exc:
        logger.exception("Full pipeline run crashed")
        full_pipeline_state.log(f"ERROR: {exc}")
        full_pipeline_state.finish(error=str(exc))
    finally:
        db.close()
