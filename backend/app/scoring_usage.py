import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Anthropic list pricing, USD per million tokens (anthropic.com/pricing).
# Covers only the models Settings.anthropic_model might reasonably be set to;
# a model with no pricing entry at all (not even after stripping a dated
# snapshot suffix — see _lookup_pricing) falls back to Opus 5 pricing with a
# logged warning, rather than silently reporting $0.
# Cache read/write tokens aren't priced separately here — complete_json()
# doesn't set cache_control on its requests, so those counts are always 0
# for this call path today.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
}
_DEFAULT_PRICING = _PRICING_PER_MTOK["claude-opus-5"]

# Matches a dated-snapshot suffix on an otherwise-known model id, e.g.
# "claude-haiku-4-5-20251001" -> "claude-haiku-4-5". Settings.anthropic_model
# accepts any string a user puts in .env, including an old dated ID copied
# from a past session — this still prices it correctly instead of missing
# the exact-string lookup and silently defaulting to (pricier) Opus 5 rates.
_DATED_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


def _lookup_pricing(model: str) -> tuple[float, float]:
    if model in _PRICING_PER_MTOK:
        return _PRICING_PER_MTOK[model]
    stripped = _DATED_SNAPSHOT_SUFFIX.sub("", model)
    if stripped in _PRICING_PER_MTOK:
        return _PRICING_PER_MTOK[stripped]
    logger.warning(
        "scoring_usage: no pricing entry for model %r — estimating with Opus 5 rates, "
        "which may overstate cost. Add it to _PRICING_PER_MTOK in app/scoring_usage.py.",
        model,
    )
    return _DEFAULT_PRICING


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = _lookup_pricing(model)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


class ScoringUsage:
    """Cumulative token usage and estimated spend for the Anthropic API calls
    made by Score (app/scoring/scorer.py) and Tailor (app/resume_tailor.py) —
    the two pipeline stages that route through AnthropicProvider
    (app/llm/anthropic_provider.py) when LLM_PROVIDER=anthropic. Unlike
    agent-apply (app/agent_apply_usage.py), which spends against a `claude`
    CLI subscription's rate-limit windows, this is a metered API key — there
    is no Anthropic endpoint that reports your actual account credit balance
    for a regular API key, so this tracks what this app itself has sent,
    with cost computed from each response's token counts against current
    list pricing. Treat total_cost_usd as an estimate, not a real-time
    balance. Persisted to disk so it survives a backend restart."""

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._path = path
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cost_usd = 0.0
        self.call_count = 0
        self.last_updated: datetime | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.total_input_tokens = data.get("total_input_tokens", 0)
        self.total_output_tokens = data.get("total_output_tokens", 0)
        self.total_cache_read_tokens = data.get("total_cache_read_tokens", 0)
        self.total_cache_creation_tokens = data.get("total_cache_creation_tokens", 0)
        self.total_cost_usd = data.get("total_cost_usd", 0.0)
        self.call_count = data.get("call_count", 0)
        self.last_updated = _parse_dt(data.get("last_updated"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
            "total_cost_usd": self.total_cost_usd,
            "call_count": self.call_count,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
        self._path.write_text(json.dumps(data), encoding="utf-8")

    def record_call(self, usage: dict) -> None:
        """usage: {"model", "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens"} — the shape
        AnthropicProvider.complete_json's on_usage callback builds from
        response.usage. Called from whichever thread made the request (the
        pipeline can score several jobs concurrently), hence the lock."""
        with self._lock:
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
            self.total_cache_creation_tokens += usage.get("cache_creation_input_tokens", 0) or 0
            self.total_cost_usd += estimate_cost_usd(
                usage.get("model") or "claude-opus-5", input_tokens, output_tokens
            )
            self.call_count += 1
            self.last_updated = datetime.now(timezone.utc)
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cache_read_tokens": self.total_cache_read_tokens,
                "total_cache_creation_tokens": self.total_cache_creation_tokens,
                "total_cost_usd": self.total_cost_usd,
                "call_count": self.call_count,
                "last_updated": self.last_updated,
            }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


scoring_usage = ScoringUsage(settings.scoring_usage_path)
