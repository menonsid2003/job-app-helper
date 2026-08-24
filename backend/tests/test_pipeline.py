import pytest

from app.config import settings
from app.connectors.base import Connector
from app.criteria import get_or_create_current_version, save_criteria
from app.llm.base import LLMProvider
from app.models import Job, JobStatus, Score
from app.pipeline import rescore_jobs, run_pipeline
from app.pipeline_state import PipelineRunState
from app.schemas import CriteriaConfig, JobListing


class FakeConnector(Connector):
    name = "fake"

    def __init__(self, listings: list[JobListing]):
        self._listings = listings

    def search(self, criteria, on_progress=None):
        if on_progress:
            on_progress(f"fake: {len(self._listings)} listings")
        return self._listings


class FailingConnector(Connector):
    name = "failing"

    def search(self, criteria, on_progress=None):
        raise RuntimeError("network down")


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: dict | None = None):
        self.response = response or _good_response()
        self.calls = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        return self.response


def _good_response(score: int = 80) -> dict:
    return {
        "score": score,
        "reasoning": "Good fit.",
        "matched_keywords": [],
        "missing_requirements": [],
        "role_category": "Data Engineer",
        "red_flags": [],
        "work_authorization": {
            "citizenship_required": False,
            "security_clearance_required": False,
            "sponsorship_mentioned": "not_mentioned",
            "hard_exclude": False,
        },
    }


def _listing(title="Data Engineer", company="Acme", url="https://example.com/1") -> JobListing:
    return JobListing(
        source="fake",
        source_url=url,
        title=title,
        company=company,
        location="Remote",
        description="Build data pipelines.",
    )


@pytest.fixture(autouse=True)
def _patch_pipeline_deps(monkeypatch, tmp_path):
    """Isolate pipeline.py from the real filesystem/resume/LLM for every test
    in this file — each test still supplies its own FakeLLMProvider via
    _use_provider() so it can assert on call counts."""
    monkeypatch.setattr("app.pipeline.load_base_resume_text", lambda: "resume text")
    criteria_path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(target_roles=["Data Engineer"]), path=criteria_path)
    monkeypatch.setattr(settings, "criteria_config_path", criteria_path)
    monkeypatch.setattr("app.pipeline.load_criteria", lambda: CriteriaConfig(target_roles=["Data Engineer"]))
    # Default: no canonical-posting match found — most tests' short fixture
    # descriptions are well under MIN_DESCRIPTION_LENGTH, which would
    # otherwise trigger a real network call from resolve_canonical_posting.
    # Tests exercising the backfill itself override this explicitly.
    monkeypatch.setattr("app.pipeline.resolve_canonical_posting", lambda company, title: None)
    return criteria_path


@pytest.fixture
def use_provider(monkeypatch):
    def _use(provider: FakeLLMProvider) -> FakeLLMProvider:
        monkeypatch.setattr("app.pipeline._make_provider", lambda: provider)
        return provider

    return _use


def test_run_pipeline_discovers_and_scores_via_connector(db_session, use_provider):
    provider = use_provider(FakeLLMProvider())
    connector = FakeConnector([_listing()])

    result = run_pipeline(db_session, connectors=[connector])

    assert result.discovered == 1
    assert result.scored == 1
    assert provider.calls == 1
    job = db_session.query(Job).one()
    assert job.status == JobStatus.SCORED


def test_run_pipeline_splits_remote_wording_out_of_location(db_session, use_provider):
    use_provider(FakeLLMProvider())
    connector = FakeConnector([_listing()])  # default location="Remote"

    run_pipeline(db_session, connectors=[connector])

    job = db_session.query(Job).one()
    assert job.location == ""
    assert job.is_remote is True


def test_run_pipeline_prefers_is_remote_hint_over_text_parsing(db_session, use_provider):
    use_provider(FakeLLMProvider())
    listing = JobListing(
        source="fake", source_url="https://example.com/1", title="Data Engineer", company="Acme",
        location="San Francisco, CA", description="Build data pipelines.", is_remote_hint=True,
    )
    connector = FakeConnector([listing])

    run_pipeline(db_session, connectors=[connector])

    job = db_session.query(Job).one()
    assert job.location == "San Francisco, CA"
    assert job.is_remote is True


def test_run_pipeline_skips_irrelevant_listing_without_llm_call(db_session, use_provider):
    provider = use_provider(FakeLLMProvider())
    connector = FakeConnector([_listing(title="Marketing Manager")])

    result = run_pipeline(db_session, connectors=[connector])

    assert result.discovered == 1
    assert result.skipped_low_relevance == 1
    assert result.scored == 0
    assert provider.calls == 0


def test_run_pipeline_dedupes_against_existing_job(db_session, use_provider):
    existing = Job(
        source="fake", source_url="https://example.com/existing", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(existing)
    db_session.commit()

    provider = use_provider(FakeLLMProvider())
    connector = FakeConnector([_listing()])

    result = run_pipeline(db_session, connectors=[connector])

    assert result.deduped_skipped == 1
    assert result.discovered == 0
    assert provider.calls == 0


def test_run_pipeline_stops_when_state_requests_it(db_session, use_provider):
    provider = use_provider(FakeLLMProvider())
    connector = FakeConnector([_listing(url="https://example.com/1"), _listing(url="https://example.com/2")])

    state = PipelineRunState()
    state.reset_for_new_run()
    state.request_stop()

    result = run_pipeline(db_session, connectors=[connector], state=state)

    # Stop is checked before each listing, so nothing should have been processed.
    assert result.discovered == 0
    assert provider.calls == 0


def test_run_pipeline_continues_after_connector_error(db_session, use_provider):
    provider = use_provider(FakeLLMProvider())

    result = run_pipeline(db_session, connectors=[FailingConnector()])

    assert result.discovered == 0
    assert provider.calls == 0
    assert len(result.errors) == 1
    assert "network down" in result.errors[0]


def test_rescore_jobs_rescopes_stale_scores_and_skips_current(db_session, use_provider, _patch_pipeline_deps):
    old_version = get_or_create_current_version(db_session)

    stale_job = Job(
        source="fake", source_url="https://example.com/stale", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(stale_job)
    db_session.commit()
    db_session.refresh(stale_job)
    db_session.add(Score(
        job_id=stale_job.id, score=50, reasoning="old", role_category="Other",
        work_authorization={}, model_used="test", criteria_version_id=old_version.id,
    ))
    db_session.commit()

    # Change criteria so a new version gets created on the next lookup.
    save_criteria(CriteriaConfig(target_roles=["Software Engineer"]), path=_patch_pipeline_deps)

    provider = use_provider(FakeLLMProvider(_good_response(score=99)))

    result = rescore_jobs(db_session)

    assert provider.calls == 1
    assert result.scored == 1
    db_session.refresh(stale_job)
    assert stale_job.scores[0].score == 99


def test_rescore_jobs_skips_jobs_already_current(db_session, use_provider):
    version = get_or_create_current_version(db_session)

    current_job = Job(
        source="fake", source_url="https://example.com/current", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(current_job)
    db_session.commit()
    db_session.refresh(current_job)
    db_session.add(Score(
        job_id=current_job.id, score=70, reasoning="current", role_category="Other",
        work_authorization={}, model_used="test", criteria_version_id=version.id,
    ))
    db_session.commit()

    provider = use_provider(FakeLLMProvider())

    result = rescore_jobs(db_session)

    assert provider.calls == 0
    assert result.scored == 0


def test_rescore_jobs_force_rescopes_current_scores_too(db_session, use_provider):
    version = get_or_create_current_version(db_session)

    current_job = Job(
        source="fake", source_url="https://example.com/current", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(current_job)
    db_session.commit()
    db_session.refresh(current_job)
    db_session.add(Score(
        job_id=current_job.id, score=70, reasoning="current", role_category="Other",
        work_authorization={}, model_used="test", criteria_version_id=version.id,
    ))
    db_session.commit()

    provider = use_provider(FakeLLMProvider(_good_response(score=99)))

    result = rescore_jobs(db_session, force=True)

    assert provider.calls == 1
    assert result.scored == 1
    db_session.refresh(current_job)
    assert current_job.scores[0].score == 99


def test_rescore_jobs_force_with_score_range_skips_jobs_outside_it(db_session, use_provider):
    version = get_or_create_current_version(db_session)

    def _make_job(url: str, score: int) -> Job:
        job = Job(
            source="fake", source_url=url, title="Data Engineer",
            company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        db_session.add(Score(
            job_id=job.id, score=score, reasoning="x", role_category="Other",
            work_authorization={}, model_used="test", criteria_version_id=version.id,
        ))
        db_session.commit()
        return job

    low_job = _make_job("https://example.com/low", 10)
    mid_job = _make_job("https://example.com/mid", 62)
    high_job = _make_job("https://example.com/high", 95)

    provider = use_provider(FakeLLMProvider(_good_response(score=77)))

    result = rescore_jobs(db_session, force=True, min_score=40, max_score=69)

    assert provider.calls == 1
    assert result.scored == 1
    db_session.refresh(low_job)
    db_session.refresh(mid_job)
    db_session.refresh(high_job)
    assert low_job.scores[0].score == 10  # untouched — below the range
    assert mid_job.scores[0].score == 77  # rescored — inside the range
    assert high_job.scores[0].score == 95  # untouched — above the range


def test_rescore_jobs_never_touches_pursued_job(db_session, use_provider):
    pursued = Job(
        source="fake", source_url="https://example.com/pursued", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.PURSUE,
    )
    db_session.add(pursued)
    db_session.commit()

    provider = use_provider(FakeLLMProvider())

    rescore_jobs(db_session)

    assert provider.calls == 0


# ---- canonical link resolution ----


def test_run_pipeline_skips_canonical_resolution_for_own_connector_sources(db_session, use_provider, monkeypatch):
    calls = []
    monkeypatch.setattr("app.pipeline.resolve_canonical_link", lambda company, title: calls.append(1) or "http://x")
    provider = use_provider(FakeLLMProvider(_good_response(score=90)))
    connector = FakeConnector([_listing()])  # source="fake" is NOT in CANONICAL_SOURCES...

    run_pipeline(db_session, connectors=[connector])

    # ...but the fake connector's source name isn't greenhouse/lever/workday,
    # so resolution *should* be attempted here — this test documents that a
    # non-canonical source does trigger it (paired with the next test, which
    # confirms real connector sources don't).
    assert provider.calls == 1
    assert calls == [1]


def test_run_pipeline_never_attempts_resolution_for_real_connector_sources(db_session, use_provider, monkeypatch):
    calls = []
    monkeypatch.setattr("app.pipeline.resolve_canonical_link", lambda company, title: calls.append(1) or "http://x")
    use_provider(FakeLLMProvider(_good_response(score=90)))
    listing = JobListing(
        source="greenhouse", source_url="https://boards.greenhouse.io/acme/jobs/1",
        title="Data Engineer", company="Acme", location="Remote", description="Build data pipelines.",
    )
    connector = FakeConnector([listing])

    run_pipeline(db_session, connectors=[connector])

    assert calls == []


def test_maybe_resolve_canonical_link_skips_below_threshold(db_session):
    from app.pipeline import _maybe_resolve_canonical_link

    job = Job(
        source="fake", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(job)
    db_session.commit()

    criteria = CriteriaConfig(canonical_link_score_threshold=70)
    _maybe_resolve_canonical_link(db_session, job, score_value=50, criteria=criteria)

    assert job.canonical_url is None


def test_maybe_resolve_canonical_link_sets_url_when_found(db_session, monkeypatch):
    from app.pipeline import _maybe_resolve_canonical_link

    monkeypatch.setattr("app.pipeline.resolve_canonical_link", lambda company, title: "https://boards.greenhouse.io/acme/jobs/1")

    job = Job(
        source="fake", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
    )
    db_session.add(job)
    db_session.commit()

    criteria = CriteriaConfig(canonical_link_score_threshold=70)
    _maybe_resolve_canonical_link(db_session, job, score_value=90, criteria=criteria)

    assert job.canonical_url == "https://boards.greenhouse.io/acme/jobs/1"


def test_maybe_resolve_canonical_link_does_not_overwrite_existing(db_session, monkeypatch):
    from app.pipeline import _maybe_resolve_canonical_link

    calls = []
    monkeypatch.setattr("app.pipeline.resolve_canonical_link", lambda company, title: calls.append(1) or "http://new")

    job = Job(
        source="fake", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Remote", description="x", status=JobStatus.SCORED,
        canonical_url="http://already-set",
    )
    db_session.add(job)
    db_session.commit()

    criteria = CriteriaConfig(canonical_link_score_threshold=70)
    _maybe_resolve_canonical_link(db_session, job, score_value=90, criteria=criteria)

    assert job.canonical_url == "http://already-set"
    assert calls == []


# ---- description backfill for thin/blank listings (e.g. LinkedIn via JobSpy) ----


def test_backfills_thin_description_from_company_career_page(db_session, use_provider, monkeypatch):
    """A blank/near-blank description would otherwise fail must_have_keywords
    in is_relevant() and get silently dropped as skipped_low_relevance —
    backfilling from the company's own board rescues it before that check."""
    from app.canonical_link import CanonicalPosting

    long_description = "A" * 500 + " requires strong Python experience."
    monkeypatch.setattr(
        "app.pipeline.load_criteria",
        lambda: CriteriaConfig(target_roles=["Data Engineer"], must_have_keywords=["Python"]),
    )
    monkeypatch.setattr(
        "app.pipeline.resolve_canonical_posting",
        lambda company, title: CanonicalPosting(url="https://boards.greenhouse.io/acme/jobs/1", description=long_description),
    )
    provider = use_provider(FakeLLMProvider(_good_response(score=90)))
    thin_listing = JobListing(
        source="jobspy", source_url="https://linkedin.com/jobs/1", title="Data Engineer",
        company="Acme", location="Remote", description="",  # LinkedIn gave nothing usable
    )
    connector = FakeConnector([thin_listing])

    result = run_pipeline(db_session, connectors=[connector])

    assert result.discovered == 1
    assert result.skipped_low_relevance == 0  # would've been dropped here without the backfilled "Python" keyword
    assert provider.calls == 1
    job = db_session.query(Job).one()
    assert job.description == long_description
    assert job.canonical_url == "https://boards.greenhouse.io/acme/jobs/1"


def test_does_not_backfill_when_description_already_substantial(db_session, use_provider, monkeypatch):
    calls = []
    monkeypatch.setattr("app.pipeline.resolve_canonical_posting", lambda company, title: calls.append(1) or None)
    use_provider(FakeLLMProvider(_good_response(score=90)))
    substantial_listing = JobListing(
        source="jobspy", source_url="https://linkedin.com/jobs/1", title="Data Engineer",
        company="Acme", location="Remote", description="B" * 200,
    )
    connector = FakeConnector([substantial_listing])

    run_pipeline(db_session, connectors=[connector])

    assert calls == []


def test_does_not_backfill_for_own_connector_sources(db_session, use_provider, monkeypatch):
    """greenhouse/lever/workday already return full content directly —
    re-resolving against their own board would be a pointless round trip."""
    calls = []
    monkeypatch.setattr("app.pipeline.resolve_canonical_posting", lambda company, title: calls.append(1) or None)
    use_provider(FakeLLMProvider(_good_response(score=90)))
    thin_listing = JobListing(
        source="greenhouse", source_url="https://boards.greenhouse.io/acme/jobs/1", title="Data Engineer",
        company="Acme", location="Remote", description="",
    )
    connector = FakeConnector([thin_listing])

    run_pipeline(db_session, connectors=[connector])

    assert calls == []


def test_backfill_keeps_original_description_when_no_better_match(db_session, use_provider, monkeypatch):
    monkeypatch.setattr("app.pipeline.resolve_canonical_posting", lambda company, title: None)
    use_provider(FakeLLMProvider(_good_response(score=90)))
    thin_listing = JobListing(
        source="jobspy", source_url="https://linkedin.com/jobs/1", title="Data Engineer",
        company="Acme", location="Remote", description="short",
    )
    connector = FakeConnector([thin_listing])

    run_pipeline(db_session, connectors=[connector])

    job = db_session.query(Job).one()
    assert job.description == "short"
    assert job.canonical_url is None
