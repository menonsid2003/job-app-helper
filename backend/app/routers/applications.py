import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auto_apply import run_auto_apply
from app.auto_apply_state import auto_apply_state
from app.criteria import load_criteria
from app.db import SessionLocal, get_db
from app.full_pipeline_state import full_pipeline_state
from app.models import Application, ApplicationStatus
from app.pipeline_state import RunStatus
from app.schemas import ApplicationOut, AutoApplyStatusOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["applications"])


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(db: Session = Depends(get_db)) -> list[ApplicationOut]:
    applications = db.execute(select(Application).order_by(Application.created_at.desc())).scalars().all()
    # job_title/job_company aren't columns on Application itself (see the
    # job relationship) — the log otherwise only shows a bare job_id, which
    # tells you nothing about which posting an entry was for.
    return [
        ApplicationOut(
            id=a.id, job_id=a.job_id, job_title=a.job.title, job_company=a.job.company,
            resume_id=a.resume_id, status=a.status, method=a.method, notes=a.notes,
            screenshot_path=a.screenshot_path, submitted_at=a.submitted_at, created_at=a.created_at,
        )
        for a in applications
    ]


@router.get("/applications/{application_id}/screenshot")
def get_application_screenshot(application_id: int, db: Session = Depends(get_db)) -> FileResponse:
    application = db.get(Application, application_id)
    if application is None or not application.screenshot_path:
        raise HTTPException(status_code=404, detail="No screenshot for this application")
    return FileResponse(application.screenshot_path, media_type="image/png")


@router.get("/auto-apply/status", response_model=AutoApplyStatusOut)
def get_auto_apply_status() -> AutoApplyStatusOut:
    return AutoApplyStatusOut(**auto_apply_state.snapshot())


@router.post("/auto-apply/stop", response_model=AutoApplyStatusOut)
def stop_auto_apply() -> AutoApplyStatusOut:
    auto_apply_state.request_stop()
    return AutoApplyStatusOut(**auto_apply_state.snapshot())


@router.post("/auto-apply/run", response_model=AutoApplyStatusOut, status_code=202)
def trigger_auto_apply(background_tasks: BackgroundTasks) -> AutoApplyStatusOut:
    if auto_apply_state.status == RunStatus.RUNNING:
        return AutoApplyStatusOut(**auto_apply_state.snapshot())
    if full_pipeline_state.status == RunStatus.RUNNING:
        raise HTTPException(status_code=400, detail="A Full Pipeline run is already in progress — wait for it to finish first.")

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

    auto_apply_state.reset_for_new_run()
    background_tasks.add_task(_run_in_background)
    return AutoApplyStatusOut(**auto_apply_state.snapshot())


def _run_in_background() -> None:
    db = SessionLocal()

    def on_result(application: Application) -> None:
        if application.status == ApplicationStatus.SUBMITTED:
            auto_apply_state.submitted_count += 1
        elif application.status == ApplicationStatus.FAILED:
            auto_apply_state.failed_count += 1
        else:
            auto_apply_state.unsupported_count += 1

    try:
        criteria = load_criteria()
        run_auto_apply(
            db, criteria,
            log=auto_apply_state.log,
            should_stop=auto_apply_state.should_stop,
            on_result=on_result,
        )
        if auto_apply_state.should_stop():
            auto_apply_state.finish(stopped=True)
        else:
            auto_apply_state.finish()
    except Exception as exc:
        logger.exception("Auto-apply run crashed")
        auto_apply_state.log(f"ERROR: {exc}")
        auto_apply_state.finish(error=str(exc))
    finally:
        db.close()
