import pytest
from fastapi.testclient import TestClient

import app.routers.pipeline as pipeline_router
from app.main import app
from app.pipeline_state import RunStatus, pipeline_state


@pytest.fixture(autouse=True)
def _reset_global_pipeline_state():
    pipeline_state.status = RunStatus.IDLE
    pipeline_state.logs = []
    pipeline_state.error = None
    pipeline_state.stop_requested = False
    yield
    pipeline_state.status = RunStatus.IDLE
    pipeline_state.logs = []
    pipeline_state.error = None
    pipeline_state.stop_requested = False


def test_trigger_pipeline_run_returns_202_and_running_status(monkeypatch):
    monkeypatch.setattr(pipeline_router, "run_pipeline_in_background", lambda: None)
    client = TestClient(app)

    response = client.post("/api/pipeline/run")

    assert response.status_code == 202
    assert response.json()["status"] == "running"


def test_trigger_pipeline_run_does_not_restart_if_already_running(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline_router, "run_pipeline_in_background", lambda: calls.append(1))
    pipeline_state.reset_for_new_run()  # simulate an in-progress run
    client = TestClient(app)

    response = client.post("/api/pipeline/run")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert calls == []


def test_get_pipeline_status_reflects_state():
    pipeline_state.reset_for_new_run()
    pipeline_state.log("Doing something")
    client = TestClient(app)

    response = client.get("/api/pipeline/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert "Doing something" in body["logs"][-1]


def test_trigger_rescore_returns_202_and_running_status(monkeypatch):
    monkeypatch.setattr(pipeline_router, "run_rescore_in_background", lambda force=False, min_score=None, max_score=None: None)
    client = TestClient(app)

    response = client.post("/api/pipeline/rescore")

    assert response.status_code == 202
    assert response.json()["status"] == "running"


def test_trigger_rescore_passes_force_flag_through(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline_router,
        "run_rescore_in_background",
        lambda force=False, min_score=None, max_score=None: calls.append(force),
    )
    client = TestClient(app)

    client.post("/api/pipeline/rescore?force=true")

    assert calls == [True]


def test_trigger_rescore_defaults_force_to_false(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline_router,
        "run_rescore_in_background",
        lambda force=False, min_score=None, max_score=None: calls.append(force),
    )
    client = TestClient(app)

    client.post("/api/pipeline/rescore")

    assert calls == [False]


def test_trigger_rescore_passes_score_range_through(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline_router,
        "run_rescore_in_background",
        lambda force=False, min_score=None, max_score=None: calls.append((min_score, max_score)),
    )
    client = TestClient(app)

    client.post("/api/pipeline/rescore?force=true&min_score=40&max_score=69")

    assert calls == [(40, 69)]


def test_stop_pipeline_sets_stop_requested_while_running():
    pipeline_state.reset_for_new_run()
    client = TestClient(app)

    response = client.post("/api/pipeline/stop")

    assert response.status_code == 200
    assert response.json()["stop_requested"] is True
    assert response.json()["status"] == "running"  # stop is a request, not immediate


def test_stop_pipeline_is_a_noop_when_idle():
    client = TestClient(app)

    response = client.post("/api/pipeline/stop")

    assert response.status_code == 200
    assert response.json()["stop_requested"] is False
