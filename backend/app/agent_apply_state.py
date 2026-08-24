import threading
from datetime import datetime, timezone

from app.pipeline_state import RunStatus


class AgentApplyRunState:
    """Same shape as AutoApplyRunState (app/auto_apply_state.py) — kept as a
    separate instance rather than shared, since agent-apply is a distinct
    run you can trigger independently of (and possibly alongside) the
    adapter-based auto-apply, and conflating their run states would make
    "is something running right now" ambiguous for either one."""

    MAX_LOGS = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: RunStatus = RunStatus.IDLE
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.current_step: str = ""
        self.logs: list[str] = []
        self.error: str | None = None
        self.stop_requested: bool = False
        self.submitted_count: int = 0
        self.failed_count: int = 0
        self.unsupported_count: int = 0
        self.total_cost_usd: float = 0.0

    def reset_for_new_run(self) -> None:
        with self._lock:
            self.status = RunStatus.RUNNING
            self.started_at = datetime.now(timezone.utc)
            self.finished_at = None
            self.current_step = "Starting…"
            self.logs = []
            self.error = None
            self.stop_requested = False
            self.submitted_count = 0
            self.failed_count = 0
            self.unsupported_count = 0
            self.total_cost_usd = 0.0

    def log(self, message: str) -> None:
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.logs.append(f"[{timestamp}] {message}")
            if len(self.logs) > self.MAX_LOGS:
                self.logs = self.logs[-self.MAX_LOGS :]
            self.current_step = message

    def request_stop(self) -> None:
        with self._lock:
            if self.status != RunStatus.RUNNING:
                return
            self.stop_requested = True
        self.log("Stop requested — will halt after the application(s) currently in progress…")

    def should_stop(self) -> bool:
        with self._lock:
            return self.stop_requested

    def add_cost(self, amount: float) -> None:
        """Called from worker threads (agent-apply can run several Chrome
        instances in parallel), so this needs its own lock rather than a
        bare += from the caller."""
        with self._lock:
            self.total_cost_usd += amount

    def finish(self, error: str | None = None, stopped: bool = False) -> None:
        with self._lock:
            if stopped:
                self.status = RunStatus.STOPPED
            else:
                self.status = RunStatus.ERROR if error else RunStatus.DONE
            self.finished_at = datetime.now(timezone.utc)
            self.error = error
            self.current_step = "Stopped" if stopped else (f"Failed: {error}" if error else "Done")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "current_step": self.current_step,
                "logs": list(self.logs),
                "error": self.error,
                "stop_requested": self.stop_requested,
                "submitted_count": self.submitted_count,
                "failed_count": self.failed_count,
                "unsupported_count": self.unsupported_count,
                "total_cost_usd": self.total_cost_usd,
            }


agent_apply_state = AgentApplyRunState()
