import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


class AgentApplyUsage:
    """Cumulative token usage and the last-seen Claude subscription 5-hour
    rate-limit window status for the agent-apply path — the only part of
    this app that spends against your `claude` CLI subscription rather than
    a metered API key (LLM scoring goes through Ollama/the Anthropic API
    directly). Persisted to disk rather than kept only in AgentApplyRunState,
    which zeroes its counters at the start of every new run — this instead
    accumulates across runs and survives a backend restart, so it can be
    shown on pages (Jobs/Tracking) that have nothing to do with an
    in-progress run."""

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._path = path
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cost_usd = 0.0
        self.job_count = 0
        self.rate_limit_status: str | None = None
        self.rate_limit_resets_at: datetime | None = None
        self.rate_limit_type: str | None = None
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
        self.job_count = data.get("job_count", 0)
        self.rate_limit_status = data.get("rate_limit_status")
        self.rate_limit_type = data.get("rate_limit_type")
        self.rate_limit_resets_at = _parse_dt(data.get("rate_limit_resets_at"))
        self.last_updated = _parse_dt(data.get("last_updated"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
            "total_cost_usd": self.total_cost_usd,
            "job_count": self.job_count,
            "rate_limit_status": self.rate_limit_status,
            "rate_limit_type": self.rate_limit_type,
            "rate_limit_resets_at": self.rate_limit_resets_at.isoformat() if self.rate_limit_resets_at else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
        self._path.write_text(json.dumps(data), encoding="utf-8")

    def record_job(self, usage: dict) -> None:
        """usage is the dict run_job/worker_loop builds: token counts plus,
        if present, rate_limit_status/rate_limit_resets_at (unix seconds)/
        rate_limit_type and cost_usd. Called from worker threads (agent-apply
        can run several Chrome instances in parallel), hence the lock."""
        with self._lock:
            self.total_input_tokens += usage.get("input_tokens", 0) or 0
            self.total_output_tokens += usage.get("output_tokens", 0) or 0
            self.total_cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
            self.total_cache_creation_tokens += usage.get("cache_creation_input_tokens", 0) or 0
            self.total_cost_usd += usage.get("cost_usd", 0.0) or 0.0
            self.job_count += 1
            if "rate_limit_status" in usage:
                self.rate_limit_status = usage.get("rate_limit_status")
                self.rate_limit_type = usage.get("rate_limit_type")
                resets_at = usage.get("rate_limit_resets_at")
                self.rate_limit_resets_at = (
                    datetime.fromtimestamp(resets_at, tz=timezone.utc) if resets_at else None
                )
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
                "job_count": self.job_count,
                "rate_limit_status": self.rate_limit_status,
                "rate_limit_type": self.rate_limit_type,
                "rate_limit_resets_at": self.rate_limit_resets_at,
                "last_updated": self.last_updated,
            }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


agent_apply_usage = AgentApplyUsage(settings.agent_apply_usage_path)
