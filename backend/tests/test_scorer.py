from app.llm.base import LLMProvider
from app.models import Job, JobStatus
from app.schemas import CriteriaConfig
from app.scoring.scorer import compute_hard_exclude, normalize_role_category, score_job


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        return self.response


def _make_job(db_session, description="A great job.") -> Job:
    job = Job(
        source="greenhouse",
        source_url="https://example.com/1",
        title="Data Engineer",
        company="Acme",
        location="Remote",
        description=description,
        status=JobStatus.DISCOVERED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _good_llm_response(**work_auth_overrides) -> dict:
    work_auth = {
        "citizenship_required": False,
        "security_clearance_required": False,
        "sponsorship_mentioned": "not_mentioned",
        "hard_exclude": False,
    }
    work_auth.update(work_auth_overrides)
    return {
        "score": 82,
        "reasoning": "Strong skills overlap.",
        "matched_keywords": ["Python", "SQL"],
        "missing_requirements": ["Spark"],
        "role_category": "Data Engineer",
        "red_flags": [],
        "work_authorization": work_auth,
    }


def test_compute_hard_exclude_true_when_citizenship_required():
    assert compute_hard_exclude({"citizenship_required": True, "security_clearance_required": False, "sponsorship_mentioned": "not_mentioned"})


def test_compute_hard_exclude_false_when_sponsorship_not_mentioned():
    assert not compute_hard_exclude(
        {"citizenship_required": False, "security_clearance_required": False, "sponsorship_mentioned": "not_mentioned"}
    )


def test_compute_hard_exclude_true_when_sponsorship_explicitly_refused():
    assert compute_hard_exclude(
        {"citizenship_required": False, "security_clearance_required": False, "sponsorship_mentioned": "no"}
    )


def test_score_job_prefilter_hit_skips_llm_call(db_session):
    job = _make_job(db_session, description="Must be a U.S. citizen to apply.")
    criteria = CriteriaConfig(
        work_authorization={"hard_exclude_prefilter_keywords": ["must be a u.s. citizen"]}
    )
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert provider.calls == 0
    assert job.status == JobStatus.EXCLUDED
    assert score.work_authorization["hard_exclude"] is True
    assert score.model_used == "prefilter"


def test_score_job_calls_llm_and_scores_when_no_prefilter_hit(db_session):
    job = _make_job(db_session)
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert provider.calls == 1
    assert job.status == JobStatus.SCORED
    assert score.score == 82
    assert score.role_category == "Data Engineer"


def test_score_job_excludes_when_llm_says_sponsorship_refused_even_if_llm_forgot_hard_exclude_flag(db_session):
    job = _make_job(db_session)
    criteria = CriteriaConfig()
    # LLM incorrectly leaves hard_exclude False despite sponsorship_mentioned == "no";
    # the scorer must recompute hard_exclude itself rather than trust the LLM's own flag.
    provider = FakeLLMProvider(_good_llm_response(sponsorship_mentioned="no", hard_exclude=False))

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.status == JobStatus.EXCLUDED
    assert score.work_authorization["hard_exclude"] is True


def test_normalize_role_category_passes_through_known_value():
    assert normalize_role_category("Data Engineer") == "Data Engineer"


def test_normalize_role_category_falls_back_to_other_for_unknown_value():
    # Small local models don't always respect the closed set we ask for.
    assert normalize_role_category("Sales") == "Other"


def test_score_job_normalizes_out_of_enum_role_category(db_session):
    job = _make_job(db_session)
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response())
    provider.response["role_category"] = "Sales"

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert score.role_category == "Other"


def test_score_job_accepts_a_custom_role_category_from_criteria(db_session):
    job = _make_job(db_session)
    criteria = CriteriaConfig(role_categories=["ICU", "ER", "Med-Surg"])
    provider = FakeLLMProvider(_good_llm_response())
    provider.response["role_category"] = "ICU"

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert score.role_category == "ICU"


def test_score_job_falls_back_to_other_for_a_category_outside_custom_list(db_session):
    job = _make_job(db_session)
    criteria = CriteriaConfig(role_categories=["ICU", "ER", "Med-Surg"])
    provider = FakeLLMProvider(_good_llm_response())
    provider.response["role_category"] = "Data Engineer"  # valid under the default list, not this custom one

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert score.role_category == "Other"


def test_score_job_does_not_exclude_when_sponsorship_not_mentioned(db_session):
    job = _make_job(db_session, description="No mention of sponsorship anywhere in this posting.")
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response(sponsorship_mentioned="not_mentioned"))

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.status == JobStatus.SCORED
    assert score.work_authorization["hard_exclude"] is False


# ---- Location / US-only hard constraint ----


def test_score_job_location_prefilter_hit_skips_llm_call(db_session):
    job = _make_job(db_session)
    job.location = "Bengaluru, India"
    db_session.commit()
    criteria = CriteriaConfig(exclude_location_keywords=["india"])
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert provider.calls == 0
    assert job.status == JobStatus.EXCLUDED
    assert score.model_used == "prefilter"
    assert "non-US location" in score.red_flags[0]


def test_score_job_location_prefilter_does_not_trigger_when_us_option_listed(db_session):
    job = _make_job(db_session)
    job.location = "Dublin, US-Remote"
    db_session.commit()
    criteria = CriteriaConfig(exclude_location_keywords=["dublin", "ireland"])
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert provider.calls == 1
    assert job.status == JobStatus.SCORED


def test_score_job_excludes_when_llm_says_location_not_us_eligible(db_session):
    job = _make_job(db_session)
    job.location = "Remote"  # ambiguous — cheap prefilter can't tell, LLM must judge
    db_session.commit()
    criteria = CriteriaConfig()
    response = _good_llm_response()
    response["location_us_eligible"] = False
    provider = FakeLLMProvider(response)

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.status == JobStatus.EXCLUDED
    assert any("non-US location" in flag for flag in score.red_flags)


def test_score_job_defaults_to_us_eligible_when_llm_omits_field(db_session):
    job = _make_job(db_session)
    job.location = "Remote"
    db_session.commit()
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response())  # no location_us_eligible key at all

    score = score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.status == JobStatus.SCORED


# ---- is_remote (LLM reads the full description, not just the location field) ----


def test_score_job_sets_job_is_remote_true_from_llm(db_session):
    job = _make_job(db_session)
    job.location = "Austin, TX"
    job.is_remote = False  # what the connector-text heuristic guessed at discovery time
    db_session.commit()
    criteria = CriteriaConfig()
    response = _good_llm_response()
    response["is_remote"] = True  # description states this Austin-anchored role is actually remote
    provider = FakeLLMProvider(response)

    score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.is_remote is True


def test_score_job_sets_job_is_remote_false_from_llm(db_session):
    job = _make_job(db_session)
    job.is_remote = True
    db_session.commit()
    criteria = CriteriaConfig()
    response = _good_llm_response()
    response["is_remote"] = False
    provider = FakeLLMProvider(response)

    score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.is_remote is False


def test_score_job_leaves_job_is_remote_unchanged_when_llm_is_unclear(db_session):
    job = _make_job(db_session)
    job.is_remote = False
    db_session.commit()
    criteria = CriteriaConfig()
    response = _good_llm_response()  # no is_remote key at all -> None -> don't overwrite
    provider = FakeLLMProvider(response)

    score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.is_remote is False


def test_score_job_leaves_job_is_remote_unchanged_when_llm_returns_unclear_string(db_session):
    job = _make_job(db_session)
    job.is_remote = None
    db_session.commit()
    criteria = CriteriaConfig()
    response = _good_llm_response()
    response["is_remote"] = "unclear"
    provider = FakeLLMProvider(response)

    score_job(db_session, job, criteria, criteria_version_id=None, provider=provider, resume_text="resume", model_name="llama3.1:8b")

    assert job.is_remote is None


# ---- country_hint (authoritative structured data, e.g. Lever's country field) ----


def test_score_job_excludes_on_non_us_country_hint_without_calling_llm(db_session):
    job = _make_job(db_session)
    job.location = "London, United Kingdom"  # would otherwise need the text blocklist
    db_session.commit()
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(
        db_session, job, criteria, criteria_version_id=None, provider=provider,
        resume_text="resume", model_name="llama3.1:8b", country_hint="GB",
    )

    assert provider.calls == 0
    assert job.status == JobStatus.EXCLUDED
    assert score.model_used == "prefilter"
    assert "country=GB" in score.red_flags[0]


def test_score_job_accepts_us_country_hint_and_skips_text_blocklist(db_session):
    job = _make_job(db_session)
    job.location = "Remote"
    db_session.commit()
    criteria = CriteriaConfig()
    provider = FakeLLMProvider(_good_llm_response())

    score = score_job(
        db_session, job, criteria, criteria_version_id=None, provider=provider,
        resume_text="resume", model_name="llama3.1:8b", country_hint="US",
    )

    assert provider.calls == 1
    assert job.status == JobStatus.SCORED
