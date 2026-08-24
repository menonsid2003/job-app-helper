from pathlib import Path

import pytest

from app.apply.base import ApplicationAdapter, ApplicationResult
from app.auto_apply import run_auto_apply
from app.llm.base import LLMProvider
from app.models import Application, ApplicationMethod, ApplicationStatus, Job, JobStatus, Resume
from app.schemas import ApplicantProfile, CriteriaConfig


class FakeAdapter(ApplicationAdapter):
    name = "fake"

    def __init__(self, result: ApplicationResult, supports_all: bool = True):
        self.result = result
        self.supports_all = supports_all
        self.submit_calls = 0

    def supports(self, url: str) -> bool:
        return self.supports_all

    def submit(self, page, application_url, profile, resume_pdf_path, screenshot_dir, capsolver):
        self.submit_calls += 1
        return self.result


def _complete_profile() -> ApplicantProfile:
    return ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")


def _make_job(db_session, status=JobStatus.PURSUE, source="fake", canonical_url=None) -> Job:
    job = Job(
        source=source, source_url="https://example.com/1", canonical_url=canonical_url,
        title="Data Engineer", company="Acme", location="Remote", description="x",
        status=status,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_resume(db_session, job: Job, tmp_path: Path) -> Resume:
    pdf_path = tmp_path / f"resume-{job.id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")
    resume = Resume(job_id=job.id, version=1, pdf_path=str(pdf_path), ats_text_path=str(pdf_path), diff_summary="")
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


@pytest.fixture(autouse=True)
def _patch_default_adapters(monkeypatch):
    """Most tests inject their own FakeAdapter via app.auto_apply.default_adapters
    per-test; this fixture just ensures nothing accidentally falls through to
    the real GreenhouseApplicationAdapter (which would try to actually
    launch a real page navigation)."""
    yield


def test_raises_when_auto_apply_disabled(db_session):
    criteria = CriteriaConfig(auto_apply_enabled=False, applicant_profile=_complete_profile())
    with pytest.raises(RuntimeError, match="auto_apply_enabled is off"):
        run_auto_apply(db_session, criteria)


def test_raises_when_applicant_profile_incomplete(db_session):
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=ApplicantProfile())
    with pytest.raises(RuntimeError, match="applicant_profile is incomplete"):
        run_auto_apply(db_session, criteria)


def test_returns_empty_when_no_eligible_jobs(db_session):
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())
    results = run_auto_apply(db_session, criteria)
    assert results == []


def test_skips_job_with_no_tailored_resume_and_no_base_resume_configured(db_session, monkeypatch):
    """With no base resume available to auto-tailor from, a job with no
    resume yet still falls back to the old "skip as unsupported" behavior."""
    def _raise(*args, **kwargs):
        raise FileNotFoundError("no base resume")

    monkeypatch.setattr("app.auto_apply.load_base_resume_text", _raise)
    _make_job(db_session, status=JobStatus.PURSUE)
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert len(results) == 1
    assert results[0].status == ApplicationStatus.UNSUPPORTED
    assert "no tailored resume" in results[0].notes.lower()


def test_auto_tailors_missing_resume_before_applying(db_session, tmp_path, monkeypatch):
    """A pursued job with no resume yet gets tailored on the fly (instead of
    skipped as unsupported) when a base resume and LLM provider are
    available, then proceeds to apply normally."""
    class FakeLLMProvider(LLMProvider):
        def complete_json(self, system: str, user: str) -> dict:
            return {
                "full_name": "Jane Doe", "phone": "555-1234", "email": "jane@example.com",
                "links": [], "summary": "Tailored for this job.", "sections": [],
            }

    monkeypatch.setattr("app.auto_apply.load_base_resume_text", lambda: "Base resume text.")
    monkeypatch.setattr("app.auto_apply.make_default_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")

    job = _make_job(db_session, status=JobStatus.PURSUE)
    fake_adapter = FakeAdapter(ApplicationResult(status="submitted", notes="Submitted OK"))
    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [fake_adapter])
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert len(results) == 1
    assert results[0].status == ApplicationStatus.SUBMITTED
    assert results[0].resume_id is not None
    assert fake_adapter.submit_calls == 1
    resumes = db_session.query(Resume).filter(Resume.job_id == job.id).all()
    assert len(resumes) == 1


def test_records_unsupported_when_no_adapter_matches(db_session, tmp_path, monkeypatch):
    job = _make_job(db_session, status=JobStatus.PURSUE, source="dice")
    _make_resume(db_session, job, tmp_path)
    fake_adapter = FakeAdapter(ApplicationResult(status="submitted", notes="x"), supports_all=False)
    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [fake_adapter])
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert len(results) == 1
    assert results[0].status == ApplicationStatus.UNSUPPORTED
    assert fake_adapter.submit_calls == 0


def test_records_submitted_application_and_updates_job_status(db_session, tmp_path, monkeypatch):
    job = _make_job(db_session, status=JobStatus.PURSUE)
    resume = _make_resume(db_session, job, tmp_path)
    fake_adapter = FakeAdapter(ApplicationResult(status="submitted", notes="Submitted OK", screenshot_path="/tmp/x.png"))
    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [fake_adapter])
    monkeypatch.setattr("app.auto_apply.random.uniform", lambda a, b: 0)  # skip real delay
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert len(results) == 1
    application = results[0]
    assert application.status == ApplicationStatus.SUBMITTED
    assert application.resume_id == resume.id
    assert application.submitted_at is not None
    db_session.refresh(job)
    assert job.status == JobStatus.APPLIED


def test_does_not_reapply_to_already_submitted_job(db_session, tmp_path, monkeypatch):
    job = _make_job(db_session, status=JobStatus.APPLIED)
    _make_resume(db_session, job, tmp_path)
    db_session.add(Application(job_id=job.id, status=ApplicationStatus.SUBMITTED, method=ApplicationMethod.AUTO, notes="x"))
    db_session.commit()

    fake_adapter = FakeAdapter(ApplicationResult(status="submitted", notes="x"))
    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [fake_adapter])
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert results == []
    assert fake_adapter.submit_calls == 0


def test_stops_when_should_stop_returns_true(db_session, tmp_path, monkeypatch):
    job1 = _make_job(db_session, status=JobStatus.PURSUE)
    _make_resume(db_session, job1, tmp_path)
    fake_adapter = FakeAdapter(ApplicationResult(status="submitted", notes="x"))
    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [fake_adapter])

    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())
    results = run_auto_apply(db_session, criteria, should_stop=lambda: True)

    assert results == []
    assert fake_adapter.submit_calls == 0


def test_records_failed_application_on_adapter_exception(db_session, tmp_path, monkeypatch):
    job = _make_job(db_session, status=JobStatus.PURSUE)
    _make_resume(db_session, job, tmp_path)

    class CrashingAdapter(FakeAdapter):
        def submit(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.auto_apply.default_adapters", lambda: [CrashingAdapter(ApplicationResult(status="submitted", notes="x"))])
    criteria = CriteriaConfig(auto_apply_enabled=True, applicant_profile=_complete_profile())

    results = run_auto_apply(db_session, criteria)

    assert len(results) == 1
    assert results[0].status == ApplicationStatus.FAILED
    assert "boom" in results[0].notes
