import logging
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import Connector, ProgressCallback
from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.jobspy_connector import JobSpyConnector
from app.connectors.lever import LeverConnector
from app.connectors.workday import WorkdayConnector
from app.criteria import load_criteria
from app.location_parse import extract_location_from_text, parse_remote_and_location
from app.models import Job
from app.schemas import JobListing

logger = logging.getLogger(__name__)

# Sources with a persistent, per-company board API — re-fetching means
# calling that one company's own connector again and matching by
# source_url. Same set the pipeline can canonical-link-resolve against;
# see pipeline.CANONICAL_SOURCES.
BOARD_CONNECTORS: dict[str, type[Connector]] = {
    "greenhouse": GreenhouseConnector,
    "lever": LeverConnector,
    "workday": WorkdayConnector,
}


def _apply_fresh_locations(jobs: list[Job], fresh_by_url: dict[str, str]) -> tuple[int, list[Job]]:
    """Update each job's location/is_remote from fresh_by_url when found and
    it actually adds information. Returns (updated_count, still_blank) —
    still_blank is every job this pass didn't resolve, for the description
    scan pass to try next."""
    updated = 0
    still_blank = []
    for job in jobs:
        raw_location = fresh_by_url.get(job.source_url)
        if not raw_location:
            still_blank.append(job)
            continue
        is_remote, cleaned = parse_remote_and_location(raw_location)
        if not cleaned and is_remote is not True:
            # Nothing gained over what's already stored — don't churn a
            # commit for it, and give it a shot in the description pass.
            still_blank.append(job)
            continue
        job.location = cleaned
        job.is_remote = is_remote
        updated += 1
    return updated, still_blank


def _apply_description_locations(jobs: list[Job]) -> int:
    """Last-resort pass over jobs a re-fetch still couldn't place: scan each
    one's already-stored description for a "City, ST" mention (see
    extract_location_from_text). No network call — this only reads data
    already on disk, so it's free to run over everything still blank
    regardless of source. Only ever fills location; is_remote is left alone
    since a city mentioned in the body doesn't tell us remote status either
    way."""
    filled = 0
    for job in jobs:
        location = extract_location_from_text(job.description)
        if location:
            job.location = location
            filled += 1
    return filled


def backfill_locations(
    db: Session,
    log: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    board_connectors: dict[str, Connector] | None = None,
    jobspy_connector: Connector | None = None,
) -> tuple[int, int, int]:
    """For every Job with a blank location, try (in order) to re-fetch it
    from its source, then fall back to scanning its own stored description
    for a "City, ST" mention. Fills in location (+ is_remote, for the
    re-fetch passes only) when found. Returns (updated, not_found, skipped).

    Greenhouse/Lever/Workday jobs are re-fetched by calling that company's
    own board connector again — a persistent, deterministic source.

    Every other source (jobspy itself, plus the per-board source tags jobspy
    listings carry — "indeed", "linkedin", "google", ...; see
    JobSpyConnector._parse_dataframe) all come from ONE JobSpyConnector
    search covering criteria.jobspy.sites, since that's the actual unit of
    re-fetch jobspy offers — there's no stable per-company listing to target
    one job at a time. Yield here is inherently lower (postings churn off
    these boards quickly, and a fresh search may just not surface the same
    URL again) — worth noting neither Indeed's nor LinkedIn's own per-job
    detail-page fetch would help either; both source location only from
    their search-result data, never from the posting page itself, so
    there's nothing extra to gain by fetching source_url directly (checked
    against the installed jobspy source, not assumed).

    Whatever's still blank after that goes through the free description
    scan (see _apply_description_locations) — no network call, since we
    already have the description on file from when the job was discovered.

    skipped only counts jobs left with no re-fetch attempted at all:
    jobspy-family jobs when criteria.jobspy.enabled is off (still get the
    description-scan pass regardless).

    board_connectors/jobspy_connector let tests inject fakes; production
    callers leave them unset and get the real connectors."""
    if board_connectors is None:
        board_connectors = {name: cls() for name, cls in BOARD_CONNECTORS.items()}
    if jobspy_connector is None:
        jobspy_connector = JobSpyConnector()

    criteria = load_criteria()
    empty_jobs = db.execute(select(Job).where(Job.location == "")).scalars().all()

    board_jobs: dict[str, list[Job]] = {}
    jobspy_family_jobs: list[Job] = []
    for job in empty_jobs:
        if job.source in BOARD_CONNECTORS:
            board_jobs.setdefault(job.source, []).append(job)
        else:
            jobspy_family_jobs.append(job)

    if log:
        log(f"{len(empty_jobs)} job(s) with a blank location to check")

    updated = 0
    skipped = 0
    still_blank: list[Job] = []

    for source, jobs in board_jobs.items():
        if should_stop and should_stop():
            still_blank.extend(jobs)
            continue
        if log:
            log(f"{source}: re-fetching board data to check {len(jobs)} job(s)…")
        connector = board_connectors[source]
        try:
            fresh_listings = connector.search(criteria, on_progress=log)
        except Exception as exc:
            logger.warning("Location backfill: %s connector failed: %s", source, exc)
            if log:
                log(f"{source}: fetch failed ({exc}), leaving {len(jobs)} job(s) unchanged")
            still_blank.extend(jobs)
            continue

        fresh_by_url = _listings_by_url(fresh_listings)
        source_updated, source_still_blank = _apply_fresh_locations(jobs, fresh_by_url)
        updated += source_updated
        still_blank.extend(source_still_blank)
        db.commit()
        if log:
            log(f"{source}: filled in {source_updated} of {len(jobs)}")

    if jobspy_family_jobs:
        if should_stop and should_stop():
            still_blank.extend(jobspy_family_jobs)
        elif not criteria.jobspy.enabled:
            if log:
                log(
                    f"jobspy: {len(jobspy_family_jobs)} job(s) from Indeed/LinkedIn/etc. can't be re-checked — "
                    "jobspy discovery is disabled in Settings"
                )
            skipped += len(jobspy_family_jobs)
            still_blank.extend(jobspy_family_jobs)
        else:
            if log:
                log(f"jobspy: re-searching to check {len(jobspy_family_jobs)} job(s) from Indeed/LinkedIn/etc…")
            try:
                fresh_listings = jobspy_connector.search(criteria, on_progress=log)
            except Exception as exc:
                logger.warning("Location backfill: jobspy connector failed: %s", exc)
                if log:
                    log(f"jobspy: search failed ({exc}), leaving {len(jobspy_family_jobs)} job(s) unchanged")
                still_blank.extend(jobspy_family_jobs)
            else:
                fresh_by_url = _listings_by_url(fresh_listings)
                jobspy_updated, jobspy_still_blank = _apply_fresh_locations(jobspy_family_jobs, fresh_by_url)
                updated += jobspy_updated
                still_blank.extend(jobspy_still_blank)
                db.commit()
                if log:
                    log(f"jobspy: filled in {jobspy_updated} of {len(jobspy_family_jobs)}")

    if still_blank:
        if log:
            log(f"description scan: checking {len(still_blank)} still-blank job(s) for a stated location…")
        described = _apply_description_locations(still_blank)
        updated += described
        db.commit()
        if log:
            log(f"description scan: filled in {described} of {len(still_blank)}")
        not_found = len(still_blank) - described
    else:
        not_found = 0

    return updated, not_found, skipped


def _listings_by_url(listings: list[JobListing]) -> dict[str, str]:
    return {listing.source_url: listing.location for listing in listings if listing.location}
