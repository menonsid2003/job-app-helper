import logging

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.models import Job, JobStatus, Score
from app.schemas import CriteriaConfig, JobListing, ScoreResult
from app.scoring.prefilter import matches_hard_exclude_keyword, matches_non_us_location
from app.scoring.prompts import build_scoring_prompt

logger = logging.getLogger(__name__)

DEFAULT_ROLE_CATEGORIES = {"ServiceNow", "SWE", "Full Stack", "Data Engineer", "Other"}


def normalize_role_category(role_category: str, valid_categories: set[str] | None = None) -> str:
    """Small local models don't always respect the closed set we ask for —
    fall back to "Other" rather than persisting an arbitrary free-text value.
    valid_categories defaults to DEFAULT_ROLE_CATEGORIES for standalone callers;
    score_job passes criteria.role_categories (plus "Other") so this reflects
    whatever field the user has actually configured."""
    valid_categories = valid_categories or DEFAULT_ROLE_CATEGORIES
    return role_category if role_category in valid_categories else "Other"


def compute_hard_exclude(work_auth: dict) -> bool:
    return bool(
        work_auth.get("citizenship_required")
        or work_auth.get("security_clearance_required")
        or work_auth.get("sponsorship_mentioned") == "no"
    )


def _excluded_by_prefilter(
    db: Session, job: Job, reasoning: str, matched: str, model_used: str, criteria_version_id: int | None
) -> Score:
    score = Score(
        job_id=job.id,
        score=0,
        reasoning=reasoning,
        matched_keywords=[],
        missing_requirements=[],
        role_category="Other",
        red_flags=[matched],
        work_authorization={
            "citizenship_required": False,
            "security_clearance_required": False,
            "sponsorship_mentioned": "not_mentioned",
            "hard_exclude": True,
        },
        model_used=model_used,
        criteria_version_id=criteria_version_id,
    )
    job.status = JobStatus.EXCLUDED
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def score_job(
    db: Session,
    job: Job,
    criteria: CriteriaConfig,
    criteria_version_id: int | None,
    provider: LLMProvider,
    resume_text: str,
    model_name: str,
    country_hint: str | None = None,
) -> Score:
    work_auth_hit = matches_hard_exclude_keyword(job.description, criteria.work_authorization.hard_exclude_prefilter_keywords)
    if work_auth_hit is not None:
        return _excluded_by_prefilter(
            db,
            job,
            reasoning=f'Excluded by work-authorization keyword prefilter (matched: "{work_auth_hit}") before an LLM call was made.',
            matched=work_auth_hit,
            model_used="prefilter",
            criteria_version_id=criteria_version_id,
        )

    if country_hint is not None:
        # Authoritative structured data from the source platform (e.g.
        # Lever's per-posting country code) beats guessing from free text.
        if country_hint.upper() != "US":
            return _excluded_by_prefilter(
                db,
                job,
                reasoning=f'Excluded: source platform reports this posting\'s country as "{country_hint}" (not US), '
                "before an LLM call was made.",
                matched=f"non-US location (country={country_hint})",
                model_used="prefilter",
                criteria_version_id=criteria_version_id,
            )
    else:
        location_hit = matches_non_us_location(job.location, criteria.exclude_location_keywords)
        if location_hit is not None:
            return _excluded_by_prefilter(
                db,
                job,
                reasoning=f'Excluded by location prefilter: "{job.location}" matched non-US keyword "{location_hit}" '
                "before an LLM call was made.",
                matched=f"non-US location: {location_hit}",
                model_used="prefilter",
                criteria_version_id=criteria_version_id,
            )

    listing = JobListing(
        source=job.source,
        source_url=job.source_url,
        title=job.title,
        company=job.company,
        location=job.location,
        salary_text=job.salary_text,
        description=job.description,
        posted_date=job.posted_date,
    )
    system, user = build_scoring_prompt(resume_text, criteria, listing)
    raw = provider.complete_json(system, user)

    try:
        result = ScoreResult.model_validate(raw)
    except ValidationError as exc:
        logger.error("LLM output failed validation for job %s: %s", job.id, exc)
        raise

    work_auth = result.work_authorization.model_dump()
    work_auth["hard_exclude"] = compute_hard_exclude(work_auth)

    red_flags = list(result.red_flags)
    reasoning = result.reasoning
    hard_exclude = work_auth["hard_exclude"]
    if result.is_remote is not None:
        # The LLM read the full description, not just the location field —
        # more reliable than the connector-text heuristic Job.is_remote was
        # set from at discovery time (see app/location_parse.py), so it
        # wins here rather than only filling a gap.
        job.is_remote = result.is_remote
    if not result.location_us_eligible:
        hard_exclude = True
        red_flags.append("non-US location (per job description)")
        reasoning = f"{reasoning} [Excluded: description indicates this role is not eligible for US-based candidates.]"

    score = Score(
        job_id=job.id,
        score=result.score,
        reasoning=reasoning,
        matched_keywords=result.matched_keywords,
        missing_requirements=result.missing_requirements,
        role_category=normalize_role_category(result.role_category, set(criteria.role_categories) | {"Other"}),
        red_flags=red_flags,
        work_authorization=work_auth,
        model_used=model_name,
        criteria_version_id=criteria_version_id,
    )
    job.status = JobStatus.EXCLUDED if hard_exclude else JobStatus.SCORED
    db.add(score)
    db.commit()
    db.refresh(score)
    return score
