import json
import logging
from typing import Callable

import anthropic

from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Claude API client. Existing prompts (scoring, resume tailoring) already
    instruct "JSON only, no prose outside the JSON object" for OllamaProvider's
    format=json mode — reused as-is here rather than switching to output_config's
    json_schema mode, since each caller's shape differs and the prompts already
    carry the contract. One retry on unparseable output; the SDK itself already
    retries connection errors / 429 / 5xx."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "claude-opus-5",
        max_tokens: int = 4096,
        max_retries: int = 2,
        on_usage: Callable[[dict], None] | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.on_usage = on_usage
        client_kwargs: dict = {"max_retries": max_retries}
        if api_key:
            client_kwargs["api_key"] = api_key
        self.client = anthropic.Anthropic(**client_kwargs)

    def complete_json(self, system: str, user: str) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
            except anthropic.RateLimitError as exc:
                raise RuntimeError(f"Claude API rate limited: {exc}") from exc
            except anthropic.APIStatusError as exc:
                raise RuntimeError(f"Claude API request failed ({exc.status_code}): {exc}") from exc
            except anthropic.APIConnectionError as exc:
                raise RuntimeError(f"Claude API connection failed: {exc}") from exc

            # Tokens are spent whether or not the response parses as JSON below,
            # so usage is recorded here rather than after a successful parse.
            if self.on_usage:
                usage = response.usage
                self.on_usage({
                    "model": self.model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                    "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                })

            if response.stop_reason == "refusal":
                raise RuntimeError(f"Claude declined to respond (stop_reason=refusal): {response.stop_details}")

            text = "".join(block.text for block in response.content if block.type == "text")
            try:
                return _parse_json_object(text)
            except ValueError as exc:
                last_error = exc
                logger.warning("Claude returned unparseable JSON (attempt %d/2): %s", attempt, exc)

        raise RuntimeError("Claude response was not valid JSON after 2 attempts") from last_error


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])
