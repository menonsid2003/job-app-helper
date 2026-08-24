from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_db
from app.llm.base import LLMProvider
from app.main import app
from app.models import Job, JobStatus


class FakeLLMProvider(LLMProvider):
    """Returns a minimal valid ResumeContent (app/resume_tailor.py) with the
    given text as the summary — flatten_resume_content then renders it as
    "Test Candidate\\n\\n{tailored_text}", which is what the router tests
    below check for."""

    def __init__(self, tailored_text: str = "Tailored resume content."):
        self.tailored_text = tailored_text
        self.last_user: str | None = None

    def complete_json(self, system: str, user: str) -> dict:
        self.last_user = user
        return {
            "full_name": "Test Candidate", "phone": "", "email": "", "links": [],
            "summary": self.tailored_text, "sections": [],
        }


def _make_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _seed_job(db_session) -> Job:
    job = Job(
        source="greenhouse", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Remote", description="Build data pipelines.",
        status=JobStatus.PURSUE,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_tailor_resume_creates_version_and_files(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.\n\nSkills\n- Python\n", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    resumes_dir = tmp_path / "resumes"
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", resumes_dir)
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))

    response = client.post(f"/api/jobs/{job.id}/resumes")

    assert response.status_code == 201
    data = response.json()
    assert data["job_id"] == job.id
    assert data["version"] == 1
    assert "added" in data["diff_summary"] or "removed" in data["diff_summary"]

    # Files actually landed on disk.
    pdf_files = list((resumes_dir / str(job.id)).glob("*.pdf"))
    txt_files = list((resumes_dir / str(job.id)).glob("*.txt"))
    assert len(pdf_files) == 1
    assert len(txt_files) == 1
    assert txt_files[0].read_text(encoding="utf-8") == "Test Candidate\n\nTailored resume content."

    app.dependency_overrides.clear()


def test_tailor_resume_bumps_job_status_from_pursue_to_tailored(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    assert job.status == JobStatus.PURSUE
    client = next(_make_client(db_session))

    client.post(f"/api/jobs/{job.id}/resumes")

    db_session.refresh(job)
    assert job.status == JobStatus.TAILORED
    app.dependency_overrides.clear()


def test_tailor_resume_does_not_regress_job_status_past_tailored(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    job.status = JobStatus.APPLIED
    db_session.commit()
    client = next(_make_client(db_session))

    # Re-tailoring (a new version) an already-applied job shouldn't knock it
    # back to "tailored".
    client.post(f"/api/jobs/{job.id}/resumes")

    db_session.refresh(job)
    assert job.status == JobStatus.APPLIED
    app.dependency_overrides.clear()


def test_tailor_resume_increments_version_on_repeat_calls(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))

    first = client.post(f"/api/jobs/{job.id}/resumes")
    second = client.post(f"/api/jobs/{job.id}/resumes")

    assert first.json()["version"] == 1
    assert second.json()["version"] == 2

    app.dependency_overrides.clear()


def test_tailor_resume_404_for_missing_job(db_session):
    client = next(_make_client(db_session))
    response = client.post("/api/jobs/9999/resumes")
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_tailor_resume_400_when_base_resume_missing(db_session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", tmp_path / "does_not_exist.txt")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))

    response = client.post(f"/api/jobs/{job.id}/resumes")

    assert response.status_code == 400
    app.dependency_overrides.clear()


def test_list_resumes_for_job_returns_versions_desc(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    client.post(f"/api/jobs/{job.id}/resumes")
    client.post(f"/api/jobs/{job.id}/resumes")

    response = client.get(f"/api/jobs/{job.id}/resumes")

    assert response.status_code == 200
    versions = [r["version"] for r in response.json()]
    assert versions == [2, 1]

    app.dependency_overrides.clear()


def test_get_resume_diff_returns_diff_lines(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Line one\nLine two", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr(
        "app.routers.resumes.make_default_provider",
        lambda: FakeLLMProvider(tailored_text="Line one\nLine two tailored"),
    )

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    resume_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]

    response = client.get(f"/api/resumes/{resume_id}/diff")

    assert response.status_code == 200
    diff = response.json()
    assert any(d["type"] == "removed" and d["text"] == "Line two" for d in diff)
    assert any(d["type"] == "added" and d["text"] == "Line two tailored" for d in diff)

    app.dependency_overrides.clear()


def test_download_resume_pdf(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    resume_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]

    response = client.get(f"/api/resumes/{resume_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"

    app.dependency_overrides.clear()


def test_download_resume_text(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider(tailored_text="Hello tailored"))

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    resume_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]

    response = client.get(f"/api/resumes/{resume_id}/text")

    assert response.status_code == 200
    assert response.text == "Test Candidate\n\nHello tailored"

    app.dependency_overrides.clear()


def test_resume_endpoints_404_for_missing_resume(db_session):
    client = next(_make_client(db_session))
    assert client.get("/api/resumes/9999/diff").status_code == 404
    assert client.get("/api/resumes/9999/pdf").status_code == 404
    assert client.get("/api/resumes/9999/text").status_code == 404
    assert client.delete("/api/resumes/9999").status_code == 404
    app.dependency_overrides.clear()


def test_delete_resume_removes_row_and_files(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    resumes_dir = tmp_path / "resumes"
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", resumes_dir)
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    resume_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]

    pdf_files = list((resumes_dir / str(job.id)).glob("*.pdf"))
    txt_files = list((resumes_dir / str(job.id)).glob("*.txt"))
    assert len(pdf_files) == 1 and len(txt_files) == 1

    response = client.delete(f"/api/resumes/{resume_id}")

    assert response.status_code == 204
    assert client.get(f"/api/resumes/{resume_id}/text").status_code == 404
    assert not pdf_files[0].exists()
    assert not txt_files[0].exists()
    assert client.get(f"/api/jobs/{job.id}/resumes").json() == []
    app.dependency_overrides.clear()


def test_delete_resume_does_not_touch_other_versions(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: FakeLLMProvider())

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    v1_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]
    v2_id = client.post(f"/api/jobs/{job.id}/resumes").json()["id"]

    client.delete(f"/api/resumes/{v1_id}")

    assert client.get(f"/api/resumes/{v1_id}/text").status_code == 404
    assert client.get(f"/api/resumes/{v2_id}/text").status_code == 200
    remaining = client.get(f"/api/jobs/{job.id}/resumes").json()
    assert [r["id"] for r in remaining] == [v2_id]
    app.dependency_overrides.clear()


def test_regenerate_with_correction_includes_previous_version_and_correction_in_prompt(
    db_session, tmp_path: Path, monkeypatch
):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")

    provider = FakeLLMProvider(tailored_text="Led the crunch project as team lead.")
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: provider)

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    client.post(f"/api/jobs/{job.id}/resumes")  # v1, no correction

    response = client.post(f"/api/jobs/{job.id}/resumes", params={"correction": "Remove the team lead claim."})

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert provider.last_user is not None
    assert "Led the crunch project as team lead." in provider.last_user  # previous version included
    assert "Remove the team lead claim." in provider.last_user
    app.dependency_overrides.clear()


def test_tailor_resume_without_correction_omits_previous_version_section(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")

    provider = FakeLLMProvider()
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: provider)

    job = _seed_job(db_session)
    client = next(_make_client(db_session))
    client.post(f"/api/jobs/{job.id}/resumes")

    assert provider.last_user is not None
    assert "previous tailored version" not in provider.last_user.lower()
    app.dependency_overrides.clear()


def test_get_base_resume_returns_empty_text_when_no_file_exists(db_session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", tmp_path / "does_not_exist.txt")
    client = next(_make_client(db_session))

    response = client.get("/api/resume/base")

    assert response.status_code == 200
    assert response.json() == {"text": ""}
    app.dependency_overrides.clear()


def test_get_base_resume_returns_existing_file_contents(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Existing resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    client = next(_make_client(db_session))

    response = client.get("/api/resume/base")

    assert response.status_code == 200
    assert response.json() == {"text": "Existing resume text."}
    app.dependency_overrides.clear()


def test_put_base_resume_writes_file_creating_parent_dirs(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "nested" / "resume" / "base_resume.txt"
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    client = next(_make_client(db_session))

    response = client.put("/api/resume/base", json={"text": "New resume content."})

    assert response.status_code == 200
    assert response.json() == {"text": "New resume content."}
    assert resume_path.read_text(encoding="utf-8") == "New resume content."
    app.dependency_overrides.clear()


def test_put_base_resume_then_get_round_trips(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    client = next(_make_client(db_session))

    client.put("/api/resume/base", json={"text": "Round trip resume."})
    response = client.get("/api/resume/base")

    assert response.json() == {"text": "Round trip resume."}
    app.dependency_overrides.clear()


def test_get_experience_bank_returns_empty_text_when_no_file_exists(db_session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.experience_bank_path", tmp_path / "does_not_exist.txt")
    client = next(_make_client(db_session))

    response = client.get("/api/resume/experience-bank")

    assert response.status_code == 200
    assert response.json() == {"text": ""}
    app.dependency_overrides.clear()


def test_put_experience_bank_then_get_round_trips(db_session, tmp_path: Path, monkeypatch):
    bank_path = tmp_path / "experience_bank.txt"
    monkeypatch.setattr("app.config.settings.experience_bank_path", bank_path)
    client = next(_make_client(db_session))

    client.put("/api/resume/experience-bank", json={"text": "Older role: Freelancer, 2019-2020."})
    response = client.get("/api/resume/experience-bank")

    assert response.json() == {"text": "Older role: Freelancer, 2019-2020."}
    assert bank_path.read_text(encoding="utf-8") == "Older role: Freelancer, 2019-2020."
    app.dependency_overrides.clear()


def test_tailor_resume_includes_experience_bank_content_in_prompt(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")

    bank_path = tmp_path / "experience_bank.txt"
    bank_path.write_text("Older role: Freelance Data Analyst, 2019-2020.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.experience_bank_path", bank_path)

    provider = FakeLLMProvider()
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: provider)

    job = _seed_job(db_session)
    client = next(_make_client(db_session))

    client.post(f"/api/jobs/{job.id}/resumes")

    assert provider.last_user is not None
    assert "Older role: Freelance Data Analyst, 2019-2020." in provider.last_user
    app.dependency_overrides.clear()


def test_tailor_resume_omits_experience_bank_section_when_unset(db_session, tmp_path: Path, monkeypatch):
    resume_path = tmp_path / "base_resume.txt"
    resume_path.write_text("Base resume text.", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.base_resume_ats_path", resume_path)
    monkeypatch.setattr("app.config.settings.tailored_resume_dir", tmp_path / "resumes")
    monkeypatch.setattr("app.config.settings.experience_bank_path", tmp_path / "does_not_exist.txt")

    provider = FakeLLMProvider()
    monkeypatch.setattr("app.routers.resumes.make_default_provider", lambda: provider)

    job = _seed_job(db_session)
    client = next(_make_client(db_session))

    client.post(f"/api/jobs/{job.id}/resumes")

    assert provider.last_user is not None
    assert "experience bank" not in provider.last_user.lower()
    app.dependency_overrides.clear()
