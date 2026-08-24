import pytest

from app.criteria import save_criteria
from app.full_pipeline import run_full_pipeline
from app.full_pipeline_state import FullPipelineRunState
from app.models import Job, JobStatus, Score
from app.schemas import ApplicantProfile, CriteriaConfig


def _job_with_score(db, score: int, status=JobStatus.SCORED, title="Data Engineer") -> Job:
    job = Job(
        source="fake", source_url=f"https://example.com/{title}-{score}", title=title, company="Acme",
        location="Austin, TX", description="x", status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    db.add(Score(
        job_id=job.id, score=score, reasoning="x", role_category="Data Engineer",
        work_authorization={}, model_used="fake",
    ))
    db.commit()
    db.refresh(job)
    return job


@pytest.fixture(autouse=True)
def _patch_criteria(monkeypatch, tmp_path):
    criteria_path = tmp_path / "criteria.yaml"
    profile = ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")
    save_criteria(CriteriaConfig(auto_apply_enabled=True, applicant_profile=profile), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)


def test_promotes_jobs_at_or_above_threshold_and_leaves_lower_scores_alone(db_session, monkeypatch):
    high = _job_with_score(db_session, score=85)
    low = _job_with_score(db_session, score=60)
    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: None)
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: [])

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    db_session.refresh(high)
    db_session.refresh(low)
    assert high.status == JobStatus.PURSUE
    assert low.status == JobStatus.SCORED
    assert state.promoted_count == 1


def test_boundary_score_equal_to_threshold_is_promoted(db_session, monkeypatch):
    job = _job_with_score(db_session, score=80)
    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: None)
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: [])

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    db_session.refresh(job)
    assert job.status == JobStatus.PURSUE


def test_only_touches_scored_jobs_not_excluded_or_already_pursued(db_session, monkeypatch):
    excluded = _job_with_score(db_session, score=95, status=JobStatus.EXCLUDED)
    already_pursued = _job_with_score(db_session, score=95, status=JobStatus.PURSUE)
    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: None)
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: [])

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    # Neither was touched by promotion (excluded stays excluded; already-pursued isn't double-counted).
    db_session.refresh(excluded)
    assert excluded.status == JobStatus.EXCLUDED
    assert state.promoted_count == 0


def test_calls_run_pipeline_before_promoting(db_session, monkeypatch):
    call_order = []
    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: call_order.append("discover_score"))
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: call_order.append("auto_apply"))

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    assert call_order == ["discover_score", "auto_apply"]
    assert state.phase == "auto_apply"


def test_stops_before_promotion_if_stop_requested_during_discover_score(db_session, monkeypatch):
    def fake_run_pipeline(db, state):
        state.request_stop()

    apply_called = []
    monkeypatch.setattr("app.full_pipeline.run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: apply_called.append(True))
    job = _job_with_score(db_session, score=95)

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    db_session.refresh(job)
    assert job.status == JobStatus.SCORED  # never promoted
    assert apply_called == []


def test_skips_auto_apply_phase_when_auto_apply_disabled(db_session, monkeypatch, tmp_path):
    criteria_path = tmp_path / "criteria2.yaml"
    save_criteria(CriteriaConfig(auto_apply_enabled=False), path=criteria_path)
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: None)
    apply_called = []
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", lambda *a, **kw: apply_called.append(True))
    job = _job_with_score(db_session, score=95)

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    db_session.refresh(job)
    assert job.status == JobStatus.PURSUE  # promotion still happens
    assert apply_called == []  # but auto-apply is skipped, not attempted
    assert state.phase == "promote"  # never advanced to auto_apply


def test_on_result_callback_updates_counters(db_session, monkeypatch):
    from app.models import Application, ApplicationMethod, ApplicationStatus

    def fake_run_auto_apply(db, criteria, log, should_stop, on_result):
        for status in [ApplicationStatus.SUBMITTED, ApplicationStatus.SUBMITTED, ApplicationStatus.FAILED, ApplicationStatus.UNSUPPORTED]:
            on_result(Application(job_id=1, status=status, method=ApplicationMethod.AUTO, notes=""))

    monkeypatch.setattr("app.full_pipeline.run_pipeline", lambda db, state: None)
    monkeypatch.setattr("app.full_pipeline.run_auto_apply", fake_run_auto_apply)

    state = FullPipelineRunState()
    state.reset_for_new_run(score_threshold=80)
    run_full_pipeline(db_session, score_threshold=80, state=state)

    assert state.submitted_count == 2
    assert state.failed_count == 1
    assert state.unsupported_count == 1
