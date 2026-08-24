from datetime import date, datetime, time

import pytest

from app.pipeline_state import RunStatus, pipeline_state
from app.scheduler import (
    _AutoScheduler,
    is_within_window,
    parse_window,
    should_auto_run,
    window_instance_date,
)
from app.schemas import CriteriaConfig


def test_parse_window_basic():
    assert parse_window("22:00-07:00") == (time(22, 0), time(7, 0))


def test_parse_window_rejects_malformed():
    with pytest.raises(ValueError):
        parse_window("not-a-window")
    with pytest.raises(ValueError):
        parse_window("25:99-07:00")


def test_is_within_window_same_day_range():
    assert is_within_window(time(12, 0), time(9, 0), time(17, 0))
    assert not is_within_window(time(8, 0), time(9, 0), time(17, 0))
    assert not is_within_window(time(18, 0), time(9, 0), time(17, 0))


def test_is_within_window_overnight_wraparound():
    start, end = time(22, 0), time(7, 0)
    assert is_within_window(time(23, 0), start, end)  # 11pm
    assert is_within_window(time(3, 0), start, end)  # 3am
    assert not is_within_window(time(12, 0), start, end)  # noon — outside


def test_window_instance_date_same_day_window():
    now = datetime(2026, 8, 22, 12, 0)
    assert window_instance_date(now, time(9, 0), time(17, 0)) == date(2026, 8, 22)


def test_window_instance_date_overnight_before_midnight():
    now = datetime(2026, 8, 22, 23, 0)  # 11pm on the 22nd
    assert window_instance_date(now, time(22, 0), time(7, 0)) == date(2026, 8, 22)


def test_window_instance_date_overnight_after_midnight_is_prior_days_instance():
    now = datetime(2026, 8, 23, 3, 0)  # 3am on the 23rd — still "last night's" window
    assert window_instance_date(now, time(22, 0), time(7, 0)) == date(2026, 8, 22)


def test_should_auto_run_false_when_disabled():
    criteria = CriteriaConfig(auto_schedule_enabled=False, gpu_schedule_window="22:00-07:00")
    assert not should_auto_run(criteria, datetime(2026, 8, 22, 23, 0))


def test_should_auto_run_false_when_no_window_set():
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window=None)
    assert not should_auto_run(criteria, datetime(2026, 8, 22, 23, 0))


def test_should_auto_run_true_when_enabled_and_within_window():
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window="22:00-07:00")
    assert should_auto_run(criteria, datetime(2026, 8, 22, 23, 0))


def test_should_auto_run_false_when_enabled_but_outside_window():
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window="22:00-07:00")
    assert not should_auto_run(criteria, datetime(2026, 8, 22, 14, 0))


def test_should_auto_run_false_on_malformed_window_rather_than_raising():
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window="garbage")
    assert not should_auto_run(criteria, datetime(2026, 8, 22, 23, 0))


# ---- _AutoScheduler.check() ----


@pytest.fixture(autouse=True)
def _reset_pipeline_state():
    pipeline_state.status = RunStatus.IDLE
    pipeline_state.logs = []
    yield
    pipeline_state.status = RunStatus.IDLE
    pipeline_state.logs = []


def test_check_does_nothing_when_pipeline_already_running(monkeypatch):
    pipeline_state.status = RunStatus.RUNNING
    scheduler = _AutoScheduler()
    calls = []
    monkeypatch.setattr("app.scheduler.threading.Thread", lambda **kw: calls.append(kw) or _NoopThread())

    scheduler.check()

    assert calls == []


def test_check_triggers_run_once_per_window_occurrence(monkeypatch):
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window="22:00-07:00")
    monkeypatch.setattr("app.scheduler.load_criteria", lambda: criteria)
    monkeypatch.setattr("app.scheduler.datetime", _FixedDatetime(datetime(2026, 8, 22, 23, 0)))
    starts = []
    monkeypatch.setattr("app.scheduler.threading.Thread", lambda **kw: _NoopThread(on_start=lambda: starts.append(1)))

    scheduler = _AutoScheduler()
    scheduler.check()
    scheduler.check()  # same night — should not trigger a second time

    assert len(starts) == 1
    assert pipeline_state.status == RunStatus.RUNNING


def test_check_does_nothing_outside_window(monkeypatch):
    criteria = CriteriaConfig(auto_schedule_enabled=True, gpu_schedule_window="22:00-07:00")
    monkeypatch.setattr("app.scheduler.load_criteria", lambda: criteria)
    monkeypatch.setattr("app.scheduler.datetime", _FixedDatetime(datetime(2026, 8, 22, 14, 0)))
    starts = []
    monkeypatch.setattr("app.scheduler.threading.Thread", lambda **kw: _NoopThread(on_start=lambda: starts.append(1)))

    _AutoScheduler().check()

    assert starts == []


class _NoopThread:
    def __init__(self, on_start=None):
        self._on_start = on_start

    def start(self) -> None:
        if self._on_start:
            self._on_start()


class _FixedDatetime:
    """Drop-in for the `datetime` class inside app.scheduler that always
    returns a fixed instant from .now(), while still being the real
    datetime type for everything else the module does with it."""

    def __init__(self, fixed_now: datetime):
        self._fixed_now = fixed_now

    def now(self):
        return self._fixed_now

    def __getattr__(self, name):
        return getattr(datetime, name)
