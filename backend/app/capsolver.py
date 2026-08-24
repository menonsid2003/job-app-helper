import logging
import time

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.capsolver.com"
POLL_INTERVAL_SECONDS = 3
MAX_POLL_ATTEMPTS = 40  # ~2 minutes


class CapSolverError(Exception):
    pass


class CapSolverClient:
    """Thin wrapper around CapSolver's documented createTask/getTaskResult
    API. Built from their published API contract, not verified against a
    live key/real challenge in this build — no CapSolver key was available
    to test against. Verify with a real key before trusting this in
    production; the request/response shapes are the most likely thing to
    have drifted if CapSolver has changed their API since."""

    def __init__(self, api_key: str, timeout_seconds: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        """Returns the g-recaptcha-response token to inject into the form."""
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            task_id = self._create_task(
                client,
                {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": website_url,
                    "websiteKey": website_key,
                },
            )
            solution = self._poll_for_result(client, task_id)
            token = solution.get("gRecaptchaResponse")
            if not token:
                raise CapSolverError(f"CapSolver task {task_id} completed without a gRecaptchaResponse token")
            return token

    def solve_turnstile(self, website_url: str, website_key: str) -> str:
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            task_id = self._create_task(
                client,
                {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": website_url,
                    "websiteKey": website_key,
                },
            )
            solution = self._poll_for_result(client, task_id)
            token = solution.get("token")
            if not token:
                raise CapSolverError(f"CapSolver task {task_id} completed without a token")
            return token

    def _create_task(self, client: httpx.Client, task: dict) -> str:
        response = client.post(f"{API_BASE}/createTask", json={"clientKey": self.api_key, "task": task})
        response.raise_for_status()
        data = response.json()
        if data.get("errorId"):
            raise CapSolverError(f"CapSolver createTask error: {data.get('errorDescription', data)}")
        task_id = data.get("taskId")
        if not task_id:
            raise CapSolverError(f"CapSolver createTask response missing taskId: {data}")
        return task_id

    def _poll_for_result(self, client: httpx.Client, task_id: str) -> dict:
        for _ in range(MAX_POLL_ATTEMPTS):
            response = client.post(f"{API_BASE}/getTaskResult", json={"clientKey": self.api_key, "taskId": task_id})
            response.raise_for_status()
            data = response.json()
            if data.get("errorId"):
                raise CapSolverError(f"CapSolver getTaskResult error: {data.get('errorDescription', data)}")
            status = data.get("status")
            if status == "ready":
                return data.get("solution", {})
            if status == "failed":
                raise CapSolverError(f"CapSolver task {task_id} failed: {data}")
            time.sleep(POLL_INTERVAL_SECONDS)
        raise CapSolverError(f"CapSolver task {task_id} did not complete within the poll budget")
