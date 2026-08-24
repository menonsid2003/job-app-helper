import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical_link import resolve_canonical_link, resolve_canonical_posting
from app.config import settings
from app.connectors.base import Connector
from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.jobspy_connector import JobSpyConnector
from app.connectors.lever import LeverConnector
from app.connectors.workday import WorkdayConnector
from app.criteria import get_or_create_current_version, load_criteria
from app.db import SessionLocal
from app.dedup import find_duplicate
from app.llm.factory import make_default_provider
from app.llm.ollama_provider import OllamaProvider
from app.location_parse import parse_remote_and_location
from app.models import Job, JobStatus
from app.pipeline_state import PipelineRunState, pipeline_state
from app.resume import load_base_resume_text
from app.schemas import CriteriaConfig, JobListing, PipelineRunResult
from app.scoring.prefilter import is_relevant
from app.scoring.scorer import score_job

logger = logging.getLogger(__name__)

# Statuses eligible to be re-scored under new criteria — anything the pipeline
# itself assigned based on a scoring run. Deliberately excludes jobs the user
# has since taken action on (pursue/skip/snoozed/tailored/applied/...) so
# rescoring criteria never clobbers a decision you've already made.
RESCORABLE_STATUSES = (JobStatus.SCORED, JobStatus.EXCLUDED)

# Sources whose connector already returns the company's own hosted link —
# canonical link resolution is a no-op (and a wasted HTTP round-trip) for
# these. jobspy is deliberately excluded: it returns board links (Indeed,
# LinkedIn, ...) rather than the company's own apply page, so canonical link
# resolution is exactly the case it exists for.
CANONICAL_SOURCES = {"greenhouse", "lever", "workday"}

# Listings this thin (roughly source-provided teaser text, no requirements/
# responsibilities) get a shot at a real description from the company's own
# board before anything downstream judges them — otherwise a blank/near-
# blank description (common from board-aggregator sources like LinkedIn via
# JobSpy) fails must_have_keywords in is_relevant() every time and the
# listing is silently dropped, never even reaching an LLM score.
MIN_DESCRIPTION_LENGTH = 150


def _maybe_backfill_description(listing: JobListing, on_progress) -> str | None:
    """Mutates listing.description in place if a better one is found.
    Returns the canonical URL on success, so the caller can stash it
    directly on the Job row and skip the redundant post-scoring
    canonical-link lookup for the same job."""
    if len(listing.description.strip()) >= MIN_DESCRIPTION_LENGTH:
        return None
    if on_progress:
        on_progress(f"Thin description for {listing.title} @ {listing.company}, checking company career page…")
    match = resolve_canonical_posting(listing.company, listing.title)
    if not match:
        return None
    if len(match.description) > len(listing.description):
        listing.description = match.description
    if on_progress:
        on_progress(f"Backfilled description for {listing.title} @ {listing.company} from {match.url}")
    return match.url


def _maybe_resolve_canonical_link(db: Session, job: Job, score_value: int, criteria: CriteriaConfig) -> None:
    if job.canonical_url or job.source in CANONICAL_SOURCES:
        return
    if score_value < criteria.canonical_link_score_threshold:
        return
    canonical_url = resolve_canonical_link(job.company, job.title)
    if canonical_url:
        job.canonical_url = canonical_url
        db.commit()


def default_connectors() -> list[Connector]:
    return [GreenhouseConnector(), LeverConnector(), WorkdayConnector(), JobSpyConnector()]


def _make_provider() -> OllamaProvider:
    # Thin indirection kept so tests can monkeypatch "app.pipeline._make_provider"
    # without reaching into app.llm.factory — the shared factory (also used by
    # the resume-tailoring router) is the actual source of truth.
    return make_default_provider()


def run_pipeline(
    db: Session, connectors: list[Connector] | None = None, state: PipelineRunState | None = None
) -> PipelineRunResult:
    if state:
        state.log("Loading resume and criteria…")
    resume_text = load_base_resume_text()  # raises FileNotFoundError if missing
    criteria = load_criteria()
    version = get_or_create_current_version(db)
    provider = _make_provider()

    discovered = 0
    deduped_skipped = 0
    scored = 0
    excluded = 0
    skipped_low_relevance = 0
    errors: list[str] = []

    for connector in connectors or default_connectors():
        if state and state.should_stop():
            break

        if state:
            state.log(f"Discovering jobs via {connector.name}…")
        try:
            listings = connector.search(criteria, on_progress=state.log if state else None)
        except Exception as exc:  # connector-level failure shouldn't kill the whole run
            logger.exception("Connector %s failed", connector.name)
            errors.append(f"{connector.name}: {exc}")
            if state:
                state.log(f"ERROR: connector {connector.name} failed: {exc}")
            continue

        if state:
            state.log(f"{connector.name}: {len(listings)} listings found, filtering…")

        for listing in listings:
            if state and state.should_stop():
                state.log("Pipeline stopped by user request.")
                break

            if listing.company in criteria.exclude_companies:
                skipped_low_relevance += 1
                if state:
                    state.skipped_low_relevance = skipped_low_relevance
                continue

            if find_duplicate(db, listing) is not None:
                deduped_skipped += 1
                if state:
                    state.deduped_skipped = deduped_skipped
                continue

            canonical_url = None
            if listing.source not in CANONICAL_SOURCES:
                canonical_url = _maybe_backfill_description(listing, state.log if state else None)

            is_remote, location = parse_remote_and_location(listing.location)
            if listing.is_remote_hint is not None:
                # Authoritative over text parsing when present — same
                # precedent as country_hint. Catches e.g. a jobspy listing
                # tagged is_remote=True whose location text is a real city
                # with no "remote" wording at all, which text parsing alone
                # would otherwise read as confirmed not-remote.
                is_remote = listing.is_remote_hint
            job = Job(
                source=listing.source,
                source_url=listing.source_url,
                canonical_url=canonical_url,
                title=listing.title,
                company=listing.company,
                location=location,
                is_remote=is_remote,
                salary_text=listing.salary_text,
                description=listing.description,
                posted_date=listing.posted_date,
                status=JobStatus.DISCOVERED,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            discovered += 1
            if state:
                state.discovered = discovered

            relevant = is_relevant(
                title=job.title,
                description=job.description,
                target_roles=criteria.target_roles,
                must_have_keywords=criteria.must_have_keywords,
                exclude_keywords=criteria.exclude_keywords,
            )
            if not relevant:
                skipped_low_relevance += 1
                if state:
                    state.skipped_low_relevance = skipped_low_relevance
                continue

            if state:
                state.log(f"Evaluating: {job.title} @ {job.company}…")

            try:
                score = score_job(
                    db=db,
                    job=job,
                    criteria=criteria,
                    criteria_version_id=version.id,
                    provider=provider,
                    resume_text=resume_text,
                    model_name=settings.ollama_model,
                    country_hint=listing.country_hint,
                )
            except Exception as exc:
                logger.exception("Scoring failed for job %s (%s at %s)", job.id, job.title, job.company)
                errors.append(f"{job.company} - {job.title}: {exc}")
                if state:
                    state.log(f"ERROR scoring {job.title} @ {job.company}: {exc}")
                continue

            if job.status == JobStatus.EXCLUDED:
                excluded += 1
                if state:
                    state.excluded = excluded
                    reason = ", ".join(score.red_flags) if score.red_flags else "hard constraint"
                    state.log(f"Excluded ({reason}): {job.title} @ {job.company}")
            else:
                scored += 1
                _maybe_resolve_canonical_link(db, job, score.score, criteria)
                if state:
                    state.scored = scored
                    state.log(f"Scored {score.score}: {job.title} @ {job.company}")

    if state:
        state.log(
            f"Done: discovered {discovered}, scored {scored}, excluded {excluded}, "
            f"skipped {skipped_low_relevance}, deduped {deduped_skipped}"
            + (f", {len(errors)} error(s)" if errors else "")
        )

    return PipelineRunResult(
        discovered=discovered,
        deduped_skipped=deduped_skipped,
        scored=scored,
        excluded=excluded,
        skipped_low_relevance=skipped_low_relevance,
        errors=errors,
    )


def rescore_jobs(
    db: Session,
    state: PipelineRunState | None = None,
    force: bool = False,
    min_score: int | None = None,
    max_score: int | None = None,
) -> PipelineRunResult:
    """Re-score every job whose latest score was computed under an older
    criteria version. Does not redo the discover-time relevance gate — this
    is specifically for "I changed my criteria/keywords, re-apply them to
    what's already in the database", not full re-discovery.

    Staleness is judged purely by criteria_version_id, so a scoring-logic or
    prompt change (not a criteria.yaml edit) leaves every job looking
    up-to-date and this rescores nothing — pass force=True to rescore every
    RESCORABLE_STATUSES job regardless, for exactly that case.

    min_score/max_score (inclusive) additionally narrow either mode to jobs
    whose latest score falls in that range — e.g. skip clear-cut low scores
    that a scoring-logic fix won't change anyway, or focus on the borderline
    ("yellow", per the dashboard's score-mid styling) band only."""
    if state:
        state.log("Loading resume and criteria…")
    resume_text = load_base_resume_text()
    criteria = load_criteria()
    version = get_or_create_current_version(db)
    provider = _make_provider()

    jobs = (
        db.execute(select(Job).where(Job.status.in_(RESCORABLE_STATUSES)))
        .scalars()
        .all()
    )
    if force:
        stale_jobs = jobs
    else:
        stale_jobs = [
            job for job in jobs if not job.scores or job.scores[0].criteria_version_id != version.id
        ]

    if min_score is not None or max_score is not None:
        stale_jobs = [
            job
            for job in stale_jobs
            if job.scores
            and (min_score is None or job.scores[0].score >= min_score)
            and (max_score is None or job.scores[0].score <= max_score)
        ]

    if state:
        reason = "(forced — ignoring criteria version)" if force else "scored under an older criteria version"
        if min_score is not None or max_score is not None:
            reason += f", score in [{min_score if min_score is not None else '-inf'}, {max_score if max_score is not None else '+inf'}]"
        state.log(f"Rescoring {len(stale_jobs)} job(s) {reason}…")

    scored = 0
    excluded = 0
    errors: list[str] = []

    for job in stale_jobs:
        if state and state.should_stop():
            state.log("Rescore stopped by user request.")
            break

        if state:
            state.log(f"Rescoring: {job.title} @ {job.company}…")

        try:
            score = score_job(
                db=db,
                job=job,
                criteria=criteria,
                criteria_version_id=version.id,
                provider=provider,
                resume_text=resume_text,
                model_name=settings.ollama_model,
            )
        except Exception as exc:
            logger.exception("Rescoring failed for job %s (%s at %s)", job.id, job.title, job.company)
            errors.append(f"{job.company} - {job.title}: {exc}")
            if state:
                state.log(f"ERROR rescoring {job.title} @ {job.company}: {exc}")
            continue

        if job.status == JobStatus.EXCLUDED:
            excluded += 1
            if state:
                state.excluded = excluded
        else:
            scored += 1
            _maybe_resolve_canonical_link(db, job, score.score, criteria)
            if state:
                state.scored = scored
        if state:
            state.log(f"Rescored {score.score}: {job.title} @ {job.company}")

    if state:
        state.log(f"Rescore done: {scored} scored, {excluded} excluded" + (f", {len(errors)} error(s)" if errors else ""))

    return PipelineRunResult(
        discovered=0,
        deduped_skipped=0,
        scored=scored,
        excluded=excluded,
        skipped_low_relevance=0,
        errors=errors,
    )


def _run_in_background(runner, **kwargs) -> None:
    db = SessionLocal()
    try:
        runner(db, state=pipeline_state, **kwargs)
        if pipeline_state.should_stop():
            pipeline_state.finish(stopped=True)
        else:
            pipeline_state.finish()
    except FileNotFoundError as exc:
        pipeline_state.log(f"ERROR: {exc}")
        pipeline_state.finish(error=str(exc))
    except Exception as exc:  # last-resort guard so status never gets stuck at "running"
        logger.exception("Pipeline run crashed")
        pipeline_state.log(f"ERROR: pipeline crashed: {exc}")
        pipeline_state.finish(error=str(exc))
    finally:
        db.close()


def run_pipeline_in_background() -> None:
    """Entry point for FastAPI's BackgroundTasks. Owns its own DB session
    since the request that triggered it has already returned."""
    _run_in_background(run_pipeline)


def run_rescore_in_background(force: bool = False, min_score: int | None = None, max_score: int | None = None) -> None:
    _run_in_background(rescore_jobs, force=force, min_score=min_score, max_score=max_score)
