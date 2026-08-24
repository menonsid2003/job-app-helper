import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.llm.factory import make_default_provider
from app.models import Job, Resume
from app.pipeline_state import RunStatus
from app.resume import (
    load_base_resume_text,
    load_base_resume_text_or_empty,
    load_experience_bank_text_or_empty,
    save_base_resume_text,
    save_experience_bank_text,
)
from app.resume_diff import DiffLine
from app.resume_service import create_tailored_resume, delete_resume, get_resume_diff
from app.schemas import BaseResumeOut, BaseResumeUpdate, ResumeOut, TailorAllStatusOut
from app.tailor_all_state import tailor_all_state
from app.tailor_service import tailor_all_pursued

logger = logging.getLogger(__name__)

router = APIRouter(tags=["resumes"])


@router.get("/api/resume/base", response_model=BaseResumeOut)
def get_base_resume() -> BaseResumeOut:
    return BaseResumeOut(text=load_base_resume_text_or_empty())


@router.put("/api/resume/base", response_model=BaseResumeOut)
def update_base_resume(update: BaseResumeUpdate) -> BaseResumeOut:
    save_base_resume_text(update.text)
    return BaseResumeOut(text=update.text)


@router.get("/api/resume/experience-bank", response_model=BaseResumeOut)
def get_experience_bank() -> BaseResumeOut:
    return BaseResumeOut(text=load_experience_bank_text_or_empty())


@router.put("/api/resume/experience-bank", response_model=BaseResumeOut)
def update_experience_bank(update: BaseResumeUpdate) -> BaseResumeOut:
    save_experience_bank_text(update.text)
    return BaseResumeOut(text=update.text)


@router.post("/api/jobs/{job_id}/resumes", response_model=ResumeOut, status_code=201)
def tailor_resume_for_job(job_id: int, correction: str | None = None, db: Session = Depends(get_db)) -> ResumeOut:
    """correction: optional — when set, revises the job's most recent
    tailored version instead of tailoring fresh (see ResumePanel's
    "Regenerate with correction")."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        base_resume_text = load_base_resume_text()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider = make_default_provider()
    try:
        resume = create_tailored_resume(db, job, provider, base_resume_text, correction=correction)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Resume tailoring failed: {exc}") from exc
    return resume


@router.get("/api/resumes/tailor-all/status", response_model=TailorAllStatusOut)
def get_tailor_all_status() -> TailorAllStatusOut:
    return TailorAllStatusOut(**tailor_all_state.snapshot())


@router.post("/api/resumes/tailor-all/stop", response_model=TailorAllStatusOut)
def stop_tailor_all() -> TailorAllStatusOut:
    tailor_all_state.request_stop()
    return TailorAllStatusOut(**tailor_all_state.snapshot())


@router.post("/api/resumes/tailor-all", response_model=TailorAllStatusOut, status_code=202)
def trigger_tailor_all(background_tasks: BackgroundTasks) -> TailorAllStatusOut:
    if tailor_all_state.status == RunStatus.RUNNING:
        return TailorAllStatusOut(**tailor_all_state.snapshot())

    try:
        load_base_resume_text()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tailor_all_state.reset_for_new_run()
    background_tasks.add_task(_run_tailor_all_in_background)
    return TailorAllStatusOut(**tailor_all_state.snapshot())


def _run_tailor_all_in_background() -> None:
    db = SessionLocal()
    try:
        provider = make_default_provider()
        base_resume_text = load_base_resume_text()
        tailored, failed = tailor_all_pursued(
            db, provider, base_resume_text,
            log=tailor_all_state.log, should_stop=tailor_all_state.should_stop,
        )
        tailor_all_state.tailored_count = tailored
        tailor_all_state.failed_count = failed
        if tailor_all_state.should_stop():
            tailor_all_state.finish(stopped=True)
        else:
            tailor_all_state.finish()
    except Exception as exc:
        logger.exception("Tailor-all run crashed")
        tailor_all_state.log(f"ERROR: {exc}")
        tailor_all_state.finish(error=str(exc))
    finally:
        db.close()


@router.get("/api/jobs/{job_id}/resumes", response_model=list[ResumeOut])
def list_resumes_for_job(job_id: int, db: Session = Depends(get_db)) -> list[ResumeOut]:
    resumes = (
        db.execute(select(Resume).where(Resume.job_id == job_id).order_by(Resume.version.desc()))
        .scalars()
        .all()
    )
    return resumes


@router.get("/api/resumes/{resume_id}/diff", response_model=list[DiffLine])
def get_resume_diff_view(resume_id: int, db: Session = Depends(get_db)) -> list[DiffLine]:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    try:
        base_resume_text = load_base_resume_text()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_resume_diff(resume, base_resume_text)


@router.get("/api/resumes/{resume_id}/pdf")
def download_resume_pdf(resume_id: int, db: Session = Depends(get_db)) -> FileResponse:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return FileResponse(resume.pdf_path, media_type="application/pdf", filename=f"resume-v{resume.version}.pdf")


@router.delete("/api/resumes/{resume_id}", status_code=204)
def delete_resume_version(resume_id: int, db: Session = Depends(get_db)) -> None:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    delete_resume(db, resume)


@router.get("/api/resumes/{resume_id}/text")
def download_resume_text(resume_id: int, db: Session = Depends(get_db)) -> PlainTextResponse:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    with open(resume.ats_text_path, encoding="utf-8") as f:
        return PlainTextResponse(f.read())
