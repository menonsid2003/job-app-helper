import json

import httpx
import pytest

from app.capsolver import CapSolverClient, CapSolverError


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("app.capsolver.time.sleep", lambda seconds: None)


def test_solve_recaptcha_v2_returns_token_when_ready_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/createTask":
            assert body["task"]["type"] == "ReCaptchaV2TaskProxyLess"
            assert body["task"]["websiteKey"] == "site-key-123"
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        if request.url.path == "/getTaskResult":
            assert body["taskId"] == "task-1"
            return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "solved-token"}})
        return httpx.Response(404)

    client = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))
    token = client.solve_recaptcha_v2("https://example.com/apply", "site-key-123")

    assert token == "solved-token"


def test_solve_recaptcha_v2_polls_until_ready():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, json={"errorId": 0, "status": "processing"})
        return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "solved-token"}})

    client = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))
    token = client.solve_recaptcha_v2("https://example.com/apply", "site-key-123")

    assert token == "solved-token"
    assert calls["n"] == 3


def test_solve_recaptcha_v2_raises_on_create_task_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errorId": 1, "errorDescription": "invalid key"})

    client = CapSolverClient("bad-key", transport=httpx.MockTransport(handler))

    with pytest.raises(CapSolverError, match="invalid key"):
        client.solve_recaptcha_v2("https://example.com/apply", "site-key-123")


def test_solve_recaptcha_v2_raises_on_task_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        return httpx.Response(200, json={"errorId": 0, "status": "failed"})

    client = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))

    with pytest.raises(CapSolverError, match="failed"):
        client.solve_recaptcha_v2("https://example.com/apply", "site-key-123")


def test_solve_recaptcha_v2_raises_after_poll_budget_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        return httpx.Response(200, json={"errorId": 0, "status": "processing"})

    client = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))

    with pytest.raises(CapSolverError, match="did not complete"):
        client.solve_recaptcha_v2("https://example.com/apply", "site-key-123")


def test_solve_turnstile_returns_token():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/createTask":
            assert body["task"]["type"] == "AntiTurnstileTaskProxyLess"
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-2"})
        return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"token": "turnstile-token"}})

    client = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))
    token = client.solve_turnstile("https://example.com/apply", "site-key-456")

    assert token == "turnstile-token"
