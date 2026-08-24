from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.base import LLMProvider
from app.models import Job, JobStatus, Resume
from app.resume import load_experience_bank_text_or_empty
from app.resume_diff import compute_diff, summarize_diff
from app.resume_tailor import flatten_resume_content, render_resume_pdf, tailor_resume_content


def create_tailored_resume(
    db: Session, job: Job, provider: LLMProvider, base_resume_text: str, correction: str | None = None,
) -> Resume:
    """correction: when set, this is a targeted revision of the job's most
    recent tailored version (see ResumePanel's "Regenerate with correction")
    rather than a fresh tailoring pass — e.g. "remove the bullet about X".
    Ignored (falls back to normal tailoring) if the job has no resume yet."""
    previous_tailored_text = ""
    if correction:
        latest = db.execute(
            select(Resume).where(Resume.job_id == job.id).order_by(Resume.version.desc()).limit(1)
        ).scalar_one_or_none()
        if latest is not None:
            previous_tailored_text = Path(latest.ats_text_path).read_text(encoding="utf-8")

    content = tailor_resume_content(
        provider=provider,
        base_resume_text=base_resume_text,
        job_title=job.title,
        job_company=job.company,
        job_description=job.description,
        experience_bank_text=load_experience_bank_text_or_empty(),
        previous_tailored_text=previous_tailored_text,
        correction=correction or "",
    )
    tailored_text = flatten_resume_content(content)

    latest_version = db.execute(
        select(Resume.version).where(Resume.job_id == job.id).order_by(Resume.version.desc()).limit(1)
    ).scalar_one_or_none()
    version = (latest_version or 0) + 1

    job_dir = settings.tailored_resume_dir / str(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / f"v{version}.pdf"
    txt_path = job_dir / f"v{version}.txt"

    txt_path.write_text(tailored_text, encoding="utf-8")
    render_resume_pdf(content, pdf_path)

    diff_summary = summarize_diff(compute_diff(base_resume_text, tailored_text))

    resume = Resume(
        job_id=job.id,
        version=version,
        pdf_path=str(pdf_path),
        ats_text_path=str(txt_path),
        diff_summary=diff_summary,
    )
    db.add(resume)
    # Only advances a job that's still at "Track" (pursue) — never regresses
    # one that's already moved further along (applied/interview/...) just
    # because it got re-tailored.
    if job.status == JobStatus.PURSUE:
        job.status = JobStatus.TAILORED
    db.commit()
    db.refresh(resume)
    return resume


def get_resume_diff(resume: Resume, base_resume_text: str) -> list:
    tailored_text = Path(resume.ats_text_path).read_text(encoding="utf-8")
    return compute_diff(base_resume_text, tailored_text)


def delete_resume(db: Session, resume: Resume) -> None:
    """Removes the DB row and the on-disk PDF/text files. Does not touch
    other versions of the same job's resume, and does not change job.status
    — deleting an old version doesn't mean the job stops being "tailored"
    (another version still exists, or the applicant is handling it manually)."""
    for path_str in (resume.pdf_path, resume.ats_text_path):
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            pass  # best-effort — a locked/already-gone file shouldn't block deleting the DB row
    db.delete(resume)
    db.commit()
