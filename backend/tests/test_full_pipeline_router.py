from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auto_apply_state import auto_apply_state
from app.criteria import save_criteria
from app.db import get_db
from app.full_pipeline_state import full_pipeline_state
from app.main import app
from app.pipeline_state import RunStatus, pipeline_state
from app.schemas import ApplicantProfile, CriteriaConfig


def _make_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_states():
    for state in (full_pipeline_state, pipeline_state, auto_apply_state):
        state.status = RunStatus.IDLE
        state.logs = []
        state.stop_requested = False
    yield
    for state in (full_pipeline_state, pipeline_state, auto_apply_state):
        state.status = RunStatus.IDLE
        state.logs = []
        state.stop_requested = False


def _configured_criteria_path(tmp_path: Path, **overrides) -> Path:
    profile = overrides.pop("applicant_profile", ApplicantProfile(full_name="Jane Doe", email="jane@x.com", phone="555-1234"))
    criteria_path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(auto_apply_enabled=True, applicant_profile=profile, **overrides), path=criteria_path)
    return criteria_path


def test_trigger_rejects_when_auto_apply_disabled(db_session, tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(auto_apply_enabled=False), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run")

    assert response.status_code == 400
    assert "auto_apply_enabled is off" in response.json()["detail"]


def test_trigger_rejects_when_profile_incomplete(db_session, tmp_path, monkeypatch):
    criteria_path = _configured_criteria_path(tmp_path, applicant_profile=ApplicantProfile())
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run")

    assert response.status_code == 400
    assert "applicant_profile is incomplete" in response.json()["detail"]


def test_trigger_accepts_and_returns_running_when_configured(db_session, tmp_path, monkeypatch):
    criteria_path = _configured_criteria_path(tmp_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    monkeypatch.setattr("app.routers.pipeline.run_full_pipeline", lambda *a, **kw: None)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run?score_threshold=75")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["score_threshold"] == 75


def test_trigger_rejects_when_discover_score_already_running(db_session, tmp_path, monkeypatch):
    criteria_path = _configured_criteria_path(tmp_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    pipeline_state.reset_for_new_run()
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run")

    assert response.status_code == 400
    assert "Discover & Score run is already in progress" in response.json()["detail"]


def test_trigger_rejects_when_auto_apply_already_running(db_session, tmp_path, monkeypatch):
    criteria_path = _configured_criteria_path(tmp_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    auto_apply_state.reset_for_new_run()
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run")

    assert response.status_code == 400
    assert "Run Auto-Apply run is already in progress" in response.json()["detail"]


def test_trigger_does_not_restart_if_full_pipeline_already_running(db_session, tmp_path, monkeypatch):
    criteria_path = _configured_criteria_path(tmp_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    calls = []
    monkeypatch.setattr("app.routers.pipeline.run_full_pipeline", lambda *a, **kw: calls.append(1))
    full_pipeline_state.reset_for_new_run(score_threshold=80)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert calls == []


def test_plain_discover_score_rejected_while_full_pipeline_running(db_session):
    full_pipeline_state.reset_for_new_run(score_threshold=80)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/run")

    assert response.status_code == 400
    assert "Full Pipeline run is already in progress" in response.json()["detail"]


def test_status_endpoint_reflects_state(db_session):
    full_pipeline_state.reset_for_new_run(score_threshold=80)
    full_pipeline_state.log("Doing something")
    client = next(_make_client(db_session))

    response = client.get("/api/pipeline/full-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["score_threshold"] == 80
    assert "Doing something" in body["logs"][-1]


def test_stop_sets_stop_requested_while_running(db_session):
    full_pipeline_state.reset_for_new_run(score_threshold=80)
    client = next(_make_client(db_session))

    response = client.post("/api/pipeline/full-run/stop")

    assert response.status_code == 200
    assert response.json()["stop_requested"] is True
