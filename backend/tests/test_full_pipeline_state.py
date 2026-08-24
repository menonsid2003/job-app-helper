import pytest

from app.full_pipeline_state import FullPipelineRunState
from app.pipeline_state import RunStatus
from app.scoring_usage import scoring_usage


def test_reset_for_new_run_captures_cost_baseline(monkeypatch):
    monkeypatch.setattr(scoring_usage, "total_cost_usd", 1.50)
    state = FullPipelineRunState()

    state.reset_for_new_run(score_threshold=80)

    assert state.status == RunStatus.RUNNING
    assert state.score_threshold == 80
    assert state.cost_at_start == 1.50


def test_snapshot_reports_live_cost_delta_since_run_start(monkeypatch):
    monkeypatch.setattr(scoring_usage, "total_cost_usd", 1.50)
    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)

    scoring_usage.total_cost_usd = 1.85  # more LLM calls happened during the run

    snap = state.snapshot()
    assert snap["cost_usd_this_run"] == pytest.approx(0.35)


def test_snapshot_cost_delta_never_goes_negative(monkeypatch):
    monkeypatch.setattr(scoring_usage, "total_cost_usd", 5.00)
    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)

    scoring_usage.total_cost_usd = 4.00  # shouldn't happen, but don't show a negative

    assert state.snapshot()["cost_usd_this_run"] == 0.0


def test_set_phase_updates_phase_and_timestamp():
    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    assert state.phase == ""

    state.set_phase("discover_score")

    assert state.phase == "discover_score"
    assert state.phase_started_at is not None


def test_reset_for_new_run_clears_all_counters_from_a_prior_run():
    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    state.discovered = 5
    state.promoted_count = 2
    state.submitted_count = 1
    state.set_phase("auto_apply")

    state.reset_for_new_run(score_threshold=70)

    assert state.discovered == 0
    assert state.promoted_count == 0
    assert state.submitted_count == 0
    assert state.phase == ""
    assert state.score_threshold == 70
