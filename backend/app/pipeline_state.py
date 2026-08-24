import threading
from datetime import datetime, timezone
from enum import Enum


class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"


class PipelineRunState:
    """Single global run state (this is a single-user, self-hosted app — no
    need to track concurrent/historical runs). Written from the background
    thread doing the run, read from request-handler threads polling status;
    the lock only guards the parts that matter for a consistent snapshot."""

    MAX_LOGS = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: RunStatus = RunStatus.IDLE
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.current_step: str = ""
        self.discovered: int = 0
        self.deduped_skipped: int = 0
        self.scored: int = 0
        self.excluded: int = 0
        self.skipped_low_relevance: int = 0
        self.logs: list[str] = []
        self.error: str | None = None
        self.stop_requested: bool = False

    def reset_for_new_run(self) -> None:
        with self._lock:
            self.status = RunStatus.RUNNING
            self.started_at = datetime.now(timezone.utc)
            self.finished_at = None
            self.current_step = "Starting…"
            self.discovered = 0
            self.deduped_skipped = 0
            self.scored = 0
            self.excluded = 0
            self.skipped_low_relevance = 0
            self.logs = []
            self.error = None
            self.stop_requested = False

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
        self.log("Stop requested — will halt after the job currently in progress…")

    def should_stop(self) -> bool:
        with self._lock:
            return self.stop_requested

    def finish(self, error: str | None = None, stopped: bool = False) -> None:
        with self._lock:
            if stopped:
                self.status = RunStatus.STOPPED
            else:
                self.status = RunStatus.ERROR if error else RunStatus.DONE
            self.finished_at = datetime.now(timezone.utc)
            self.error = error
            if stopped:
                self.current_step = "Stopped"
            else:
                self.current_step = f"Failed: {error}" if error else "Done"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "current_step": self.current_step,
                "discovered": self.discovered,
                "deduped_skipped": self.deduped_skipped,
                "scored": self.scored,
                "excluded": self.excluded,
                "skipped_low_relevance": self.skipped_low_relevance,
                "logs": list(self.logs),
                "error": self.error,
                "stop_requested": self.stop_requested,
            }


pipeline_state = PipelineRunState()
