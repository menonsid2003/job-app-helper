import json
import logging
import time

import httpx

from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """Ollama /api/chat client. The Ollama host is a shared gaming PC on the LAN,
    so timeouts are generous and transient failures are retried with backoff
    rather than surfaced immediately."""

    def __init__(
        self,
        host: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def complete_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(f"{self.host}/api/chat", json=payload)
                    response.raise_for_status()
                    content = response.json()["message"]["content"]
                    return json.loads(content)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                wait = 2 ** attempt
                logger.warning(
                    "Ollama request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt,
                    self.max_retries,
                    exc,
                    wait,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)
            except (KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning("Ollama returned unparseable output (attempt %d/%d): %s", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(1)

        raise RuntimeError(f"Ollama request failed after {self.max_retries} attempts") from last_error
