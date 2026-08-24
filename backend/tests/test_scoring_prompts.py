from datetime import date

from app.schemas import CriteriaConfig, JobListing
from app.scoring.prompts import build_scoring_prompt


def _job() -> JobListing:
    return JobListing(
        source="fake",
        source_url="https://example.com/1",
        title="Data Engineer",
        company="Acme",
        location="Remote",
        description="Build data pipelines.",
    )


def test_scoring_prompt_includes_explicit_today_date():
    _, user = build_scoring_prompt("resume text", CriteriaConfig(), _job(), today=date(2026, 8, 22))

    assert "2026-08-22" in user


def test_scoring_prompt_defaults_today_to_the_real_current_date():
    _, user = build_scoring_prompt("resume text", CriteriaConfig(), _job())

    assert date.today().isoformat() in user


def test_scoring_prompt_includes_experience_gap_leniency_instructions():
    _, user = build_scoring_prompt("resume text", CriteriaConfig(), _job())

    assert "experience-gap leniency" in user.lower()
    assert "practical match" in user.lower()


def test_scoring_prompt_uses_custom_role_categories_instead_of_the_default_tech_ones():
    criteria = CriteriaConfig(role_categories=["ICU", "ER", "Med-Surg"])

    _, user = build_scoring_prompt("resume text", criteria, _job())

    assert "ICU, ER, Med-Surg, Other" in user
    assert "ServiceNow, SWE, Full Stack, Data Engineer, Other" not in user  # the old hardcoded default list


def test_scoring_prompt_always_appends_other_even_if_caller_forgets_it():
    criteria = CriteriaConfig(role_categories=["ICU", "Other", "ER"])  # "Other" already present, mid-list

    _, user = build_scoring_prompt("resume text", criteria, _job())

    assert "ICU, Other, ER" in user  # not duplicated at the end
