from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auto_apply_state import auto_apply_state
from app.criteria import save_criteria
from app.db import get_db
from app.full_pipeline_state import full_pipeline_state
from app.main import app
from app.models import Application, ApplicationMethod, ApplicationStatus, Job, JobStatus
from app.pipeline_state import RunStatus
from app.schemas import ApplicantProfile, CriteriaConfig


def _make_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_auto_apply_state():
    auto_apply_state.status = RunStatus.IDLE
    auto_apply_state.logs = []
    full_pipeline_state.status = RunStatus.IDLE
    yield
    auto_apply_state.status = RunStatus.IDLE
    auto_apply_state.logs = []
    full_pipeline_state.status = RunStatus.IDLE


def test_trigger_rejects_when_auto_apply_disabled(db_session, tmp_path: Path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(auto_apply_enabled=False), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)

    client = next(_make_client(db_session))
    response = client.post("/api/auto-apply/run")

    assert response.status_code == 400
    assert "auto_apply_enabled is off" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_trigger_rejects_when_profile_incomplete(db_session, tmp_path: Path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(auto_apply_enabled=True, applicant_profile=ApplicantProfile()), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)

    client = next(_make_client(db_session))
    response = client.post("/api/auto-apply/run")

    assert response.status_code == 400
    assert "applicant_profile is incomplete" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_trigger_rejects_when_full_pipeline_already_running(db_session, tmp_path: Path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    profile = ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")
    save_criteria(CriteriaConfig(auto_apply_enabled=True, applicant_profile=profile), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    full_pipeline_state.reset_for_new_run(score_threshold=80)

    client = next(_make_client(db_session))
    response = client.post("/api/auto-apply/run")

    assert response.status_code == 400
    assert "Full Pipeline run is already in progress" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_trigger_accepts_and_returns_running_when_configured(db_session, tmp_path: Path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    profile = ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")
    save_criteria(CriteriaConfig(auto_apply_enabled=True, applicant_profile=profile), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    monkeypatch.setattr("app.routers.applications.run_auto_apply", lambda *a, **kw: [])

    client = next(_make_client(db_session))
    response = client.post("/api/auto-apply/run")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    app.dependency_overrides.clear()


def test_status_endpoint_reflects_state(db_session):
    auto_apply_state.reset_for_new_run()
    auto_apply_state.log("Doing something")
    client = next(_make_client(db_session))

    response = client.get("/api/auto-apply/status")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert "Doing something" in response.json()["logs"][-1]
    app.dependency_overrides.clear()


def test_stop_endpoint_sets_stop_requested(db_session):
    auto_apply_state.reset_for_new_run()
    client = next(_make_client(db_session))

    response = client.post("/api/auto-apply/stop")

    assert response.json()["stop_requested"] is True
    app.dependency_overrides.clear()


def test_list_applications_returns_newest_first(db_session):
    job = Job(
        source="greenhouse", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.APPLIED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.add(Application(job_id=job.id, status=ApplicationStatus.SUBMITTED, method=ApplicationMethod.AUTO, notes="first"))
    db_session.commit()
    db_session.add(Application(job_id=job.id, status=ApplicationStatus.FAILED, method=ApplicationMethod.AUTO, notes="second"))
    db_session.commit()

    client = next(_make_client(db_session))
    response = client.get("/api/applications")

    assert response.status_code == 200
    notes = [a["notes"] for a in response.json()]
    assert notes == ["second", "first"]
    app.dependency_overrides.clear()


def test_list_applications_includes_job_title_and_company(db_session):
    """A bare job_id in the log tells you nothing about which posting it
    was — job_title/job_company let the UI actually show which job."""
    job = Job(
        source="greenhouse", source_url="https://example.com/1", title="Senior Data Engineer",
        company="Acme Corp", location="Remote", description="x", status=JobStatus.APPLIED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.add(Application(job_id=job.id, status=ApplicationStatus.SUBMITTED, method=ApplicationMethod.AGENT, notes="x"))
    db_session.commit()

    client = next(_make_client(db_session))
    response = client.get("/api/applications")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["job_title"] == "Senior Data Engineer"
    assert data[0]["job_company"] == "Acme Corp"
    app.dependency_overrides.clear()
