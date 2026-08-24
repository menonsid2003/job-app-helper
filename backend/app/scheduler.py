import logging
import threading
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.criteria import load_criteria
from app.pipeline import run_pipeline_in_background
from app.pipeline_state import RunStatus, pipeline_state
from app.schemas import CriteriaConfig

logger = logging.getLogger(__name__)

CHECK_INTERVAL_MINUTES = 30


def parse_window(window: str) -> tuple[time, time]:
    """Parse "HH:MM-HH:MM" (e.g. "22:00-07:00") into (start, end) times.
    Raises ValueError on anything malformed."""
    try:
        start_str, end_str = window.split("-", 1)
        start = datetime.strptime(start_str.strip(), "%H:%M").time()
        end = datetime.strptime(end_str.strip(), "%H:%M").time()
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"gpu_schedule_window '{window}' is not in HH:MM-HH:MM format") from exc
    return start, end


def is_within_window(now_time: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_time <= end
    # Wraps past midnight (e.g. 22:00-07:00) — "within" means at/after start
    # OR at/before end, not a contiguous start<=now<=end range.
    return now_time >= start or now_time <= end


def window_instance_date(now: datetime, start: time, end: time) -> date:
    """The calendar date identifying this specific occurrence of the window
    — for an overnight window, both 11pm tonight and 3am tomorrow morning
    belong to the same occurrence, keyed by the night it started."""
    if start <= end:
        return now.date()
    if now.time() >= start:
        return now.date()
    return now.date() - timedelta(days=1)


def should_auto_run(criteria: CriteriaConfig, now: datetime | None = None) -> bool:
    """Pure decision function (no state, no side effects) — whether *right
    now* falls inside an active auto-schedule window. Does not account for
    "already ran this occurrence" — that's the periodic checker's job, kept
    separate so this function stays trivially testable."""
    if not criteria.auto_schedule_enabled or not criteria.gpu_schedule_window:
        return False
    try:
        start, end = parse_window(criteria.gpu_schedule_window)
    except ValueError:
        logger.warning("Invalid gpu_schedule_window %r — auto-schedule disabled until fixed", criteria.gpu_schedule_window)
        return False
    return is_within_window((now or datetime.now()).time(), start, end)


class _AutoScheduler:
    """Thin stateful wrapper around should_auto_run — tracks which window
    occurrence has already fired so a 30-minute check interval doesn't
    re-trigger a run every 30 minutes all night."""

    def __init__(self) -> None:
        self._last_run_instance: date | None = None
        self._scheduler: BackgroundScheduler | None = None

    def check(self) -> None:
        if pipeline_state.status == RunStatus.RUNNING:
            return
        try:
            criteria = load_criteria()
        except FileNotFoundError:
            return

        now = datetime.now()
        if not should_auto_run(criteria, now):
            return

        start, end = parse_window(criteria.gpu_schedule_window)  # already validated by should_auto_run
        instance = window_instance_date(now, start, end)
        if instance == self._last_run_instance:
            return

        self._last_run_instance = instance
        logger.info("Auto-triggering pipeline run — within scheduled GPU window %s", criteria.gpu_schedule_window)
        pipeline_state.reset_for_new_run()
        pipeline_state.log(f"Auto-triggered: within scheduled GPU window ({criteria.gpu_schedule_window})")
        threading.Thread(target=run_pipeline_in_background, daemon=True).start()

    def start(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self.check, "interval", minutes=CHECK_INTERVAL_MINUTES, id="gpu_window_check")
        self._scheduler.start()

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None


auto_scheduler = _AutoScheduler()
