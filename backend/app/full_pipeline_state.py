import threading
from datetime import datetime, timezone

from app.pipeline_state import RunStatus
from app.scoring_usage import scoring_usage


class FullPipelineRunState:
    """Same shape/purpose as PipelineRunState and AutoApplyRunState, for the
    combined discover -> score -> promote -> auto-apply run (app/full_pipeline.py).
    Kept as its own state object (not reusing pipeline_state/auto_apply_state)
    so "is a full-pipeline run active" stays answerable independently of
    whether a plain Discover & Score or a plain Run Auto-Apply is active —
    the trigger endpoint checks all three before starting any of them, since
    two of these racing on the same Job rows would double-score or lose
    status updates.

    Exposes the exact attribute names app.pipeline.run_pipeline() writes
    directly onto whatever state object it's given (discovered/deduped_skipped/
    scored/excluded/skipped_low_relevance) so this can be passed to it as-is
    for phase 1, duck-typed the same way PipelineRunState is."""

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

        # Phase tracking — phase_started_at resets on each transition so the
        # frontend can derive a per-job-rate ETA for the auto-apply phase
        # specifically (the one phase where the total to process is actually
        # known in advance, once promoted_count is set).
        self.phase: str = ""  # "discover_score" | "promote" | "auto_apply" | ""
        self.phase_started_at: datetime | None = None

        # Phase 1: discover + score (same names run_pipeline expects)
        self.discovered: int = 0
        self.deduped_skipped: int = 0
        self.scored: int = 0
        self.excluded: int = 0
        self.skipped_low_relevance: int = 0

        # Phase 2: promote (score >= threshold -> PURSUE)
        self.promoted_count: int = 0
        self.score_threshold: int = 0

        # Phase 3: auto-apply (same names auto_apply_state uses)
        self.submitted_count: int = 0
        self.failed_count: int = 0
        self.unsupported_count: int = 0

        # Cost tracking — cost_at_start is a snapshot of the all-time
        # cumulative total (scoring_usage.py) taken when this run starts;
        # snapshot() below always recomputes the live delta against the
        # current cumulative total, so cost-so-far-in-this-run stays
        # accurate in real time with no extra plumbing from the run loop.
        self.cost_at_start: float = 0.0

    def reset_for_new_run(self, score_threshold: int) -> None:
        with self._lock:
            self.status = RunStatus.RUNNING
            self.started_at = datetime.now(timezone.utc)
            self.finished_at = None
            self.current_step = "Starting…"
            self.logs = []
            self.error = None
            self.stop_requested = False
            self.phase = ""
            self.phase_started_at = None
            self.discovered = 0
            self.deduped_skipped = 0
            self.scored = 0
            self.excluded = 0
            self.skipped_low_relevance = 0
            self.promoted_count = 0
            self.score_threshold = score_threshold
            self.submitted_count = 0
            self.failed_count = 0
            self.unsupported_count = 0
            self.cost_at_start = scoring_usage.snapshot()["total_cost_usd"]

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase
            self.phase_started_at = datetime.now(timezone.utc)

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
        self.log("Stop requested — will halt after the current job finishes…")

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
            self.current_step = "Stopped" if stopped else (f"Failed: {error}" if error else "Done")

    def snapshot(self) -> dict:
        with self._lock:
            cost_now = scoring_usage.snapshot()["total_cost_usd"]
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "current_step": self.current_step,
                "logs": list(self.logs),
                "error": self.error,
                "stop_requested": self.stop_requested,
                "phase": self.phase,
                "phase_started_at": self.phase_started_at,
                "discovered": self.discovered,
                "deduped_skipped": self.deduped_skipped,
                "scored": self.scored,
                "excluded": self.excluded,
                "skipped_low_relevance": self.skipped_low_relevance,
                "promoted_count": self.promoted_count,
                "score_threshold": self.score_threshold,
                "submitted_count": self.submitted_count,
                "failed_count": self.failed_count,
                "unsupported_count": self.unsupported_count,
                "cost_usd_this_run": max(0.0, cost_now - self.cost_at_start),
            }


full_pipeline_state = FullPipelineRunState()
