from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from playwright.sync_api import Page
from pydantic import BaseModel

from app.capsolver import CapSolverClient
from app.schemas import ApplicantProfile


class ApplicationResult(BaseModel):
    status: Literal["submitted", "failed", "unsupported"]
    notes: str
    screenshot_path: str | None = None


def resume_upload_filename(profile: ApplicantProfile) -> str:
    """Filename shown to the ATS/recruiter on upload — deliberately NOT the
    on-disk filename (data/resumes/{job_id}/v{n}.pdf), which is only ever
    looked up by job_id in the database, never by name. Matches the naming
    convention app/apply_agent/prompt.py already uses for the AI-agent path,
    so both apply paths present the same clean filename."""
    name_slug = profile.full_name.replace(" ", "_") or "Applicant"
    return f"{name_slug}_Resume.pdf"


class ApplicationAdapter(ABC):
    """One per ATS platform. Deliberately conservative: anything the adapter
    can't confidently fill (an unrecognized required question, a CAPTCHA
    with no solver configured, a missing standard field) returns
    "unsupported" rather than submitting an incomplete or guessed
    application — matches the spec's own principle for unsupported
    platforms ("fall back... rather than guessing at a submit"), just
    applied at the field level too, not only the platform level."""

    name: str

    @abstractmethod
    def supports(self, url: str) -> bool:
        ...

    @abstractmethod
    def submit(
        self,
        page: Page,
        application_url: str,
        profile: ApplicantProfile,
        resume_pdf_path: Path,
        screenshot_dir: Path,
        capsolver: CapSolverClient | None,
    ) -> ApplicationResult:
        ...
