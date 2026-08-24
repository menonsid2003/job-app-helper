import logging
from typing import Any, Callable

import pandas as pd

from app.connectors.base import Connector, ProgressCallback
from app.schemas import CriteriaConfig, JobListing

logger = logging.getLogger(__name__)

ScrapeFn = Callable[..., "pd.DataFrame"]


class JobSpyConnector(Connector):
    """Searches general job boards (Indeed, LinkedIn, ZipRecruiter, Glassdoor,
    ...) via the JobSpy library (https://github.com/speedyapply/JobSpy).
    Unlike the other connectors this has no per-company API to page through —
    it runs one search per (target_roles x locations) combination, the same
    way a person would search those sites directly. Disabled unless
    criteria.jobspy.enabled is set, since scraping these sites carries a
    different reliability/blocking profile than the official board APIs."""

    name = "jobspy"

    def __init__(self, scrape_fn: ScrapeFn | None = None) -> None:
        self._scrape_fn = scrape_fn

    def search(self, criteria: CriteriaConfig, on_progress: ProgressCallback | None = None) -> list[JobListing]:
        config = criteria.jobspy
        if not config.enabled:
            return []

        scrape = self._scrape_fn or _default_scrape_fn()

        search_terms = criteria.target_roles or [""]
        locations = criteria.locations or [""]

        listings: list[JobListing] = []
        seen_urls: set[str] = set()
        for search_term in search_terms:
            for location in locations:
                if on_progress:
                    on_progress(f"jobspy: searching '{search_term}' in '{location}'…")
                try:
                    df = scrape(
                        site_name=config.sites,
                        search_term=search_term,
                        location=location,
                        results_wanted=config.results_wanted,
                        hours_old=config.hours_old,
                        country_indeed=config.country_indeed,
                    )
                except Exception as exc:  # a blocked/rate-limited site shouldn't kill the whole search
                    logger.warning("jobspy search failed for '%s' in '%s': %s", search_term, location, exc)
                    if on_progress:
                        on_progress(f"jobspy: '{search_term}' in '{location}' failed ({exc})")
                    continue

                new_listings = _parse_dataframe(df, seen_urls)
                listings.extend(new_listings)
                if on_progress:
                    on_progress(f"jobspy: '{search_term}' in '{location}' → {len(new_listings)} new jobs")
        return listings


def _default_scrape_fn() -> ScrapeFn:
    from jobspy import scrape_jobs

    return scrape_jobs


def _parse_dataframe(df: "pd.DataFrame | None", seen_urls: set[str]) -> list[JobListing]:
    if df is None or df.empty:
        return []

    listings = []
    for _, row in df.iterrows():
        url = _clean_str(row.get("job_url")) or _clean_str(row.get("job_url_direct"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # jobspy tags every row with which board it actually came from (its
        # own "site" column: "indeed", "linkedin", "zip_recruiter", ...) —
        # surfaced directly as the job's source instead of the generic
        # "jobspy" label, so the Source column (and its filter/sort) can
        # actually distinguish them.
        source = _clean_str(row.get("site")) or "jobspy"

        listings.append(
            JobListing(
                source=source,
                source_url=url,
                title=_clean_str(row.get("title")) or "",
                company=_clean_str(row.get("company")) or "",
                location=_format_location(row),
                is_remote_hint=_remote_hint(row),
                salary_text=_format_salary(row),
                description=_clean_str(row.get("description")) or "",
                posted_date=_clean_str(row.get("date_posted")),
            )
        )
    return listings


def _clean_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    # jobspy's own LinkedIn scraper writes the literal string "N/A" (not
    # None/NaN) for title/company when it can't find the expected tag on the
    # card — e.g. jobspy/linkedin/__init__.py's `company = ... if
    # company_a_tag else "N/A"`. Left unfiltered, that gets ingested as if
    # "N/A" were the actual company name instead of being treated as
    # missing (same failure mode the location fix above addresses).
    if text.lower() == "n/a":
        return None
    return text


def _format_location(row: Any) -> str:
    # jobspy's JobPost model only has a single "location" field (already
    # formatted as "City, State" by its own Location.display_location()) —
    # there is no separate "city"/"state" column in the DataFrame for any
    # site, LinkedIn included. Reading city/state here always returned None,
    # so every non-remote posting fell through to "" — silently dropping the
    # location tag and, worse, letting matches_non_us_location's hard
    # US-only filter skip the check entirely (it treats a blank location as
    # "can't tell, let the LLM judge" rather than excluding it).
    #
    # This deliberately no longer injects the literal string "Remote" here
    # when location is blank — jobspy's own is_remote flag is carried
    # separately as is_remote_hint (see _remote_hint below) instead, so a
    # posting that lists BOTH a real location and is_remote=True (e.g. a
    # remote role anchored to "San Francisco, CA") keeps that location text
    # instead of losing it to a bare "Remote" string.
    return _clean_str(row.get("location")) or ""


def _remote_hint(row: Any) -> bool | None:
    is_remote = row.get("is_remote")
    return is_remote if isinstance(is_remote, bool) else None


def _format_salary(row: Any) -> str | None:
    min_amount = row.get("min_amount")
    max_amount = row.get("max_amount")
    has_min = min_amount is not None and not pd.isna(min_amount)
    has_max = max_amount is not None and not pd.isna(max_amount)
    if not has_min and not has_max:
        return None

    currency = _clean_str(row.get("currency")) or "USD"
    interval = _clean_str(row.get("interval")) or ""
    parts = []
    if has_min:
        parts.append(f"{int(min_amount):,}")
    if has_max:
        parts.append(f"{int(max_amount):,}")
    range_text = "-".join(parts)
    suffix = f"/{interval}" if interval else ""
    return f"{currency} {range_text}{suffix}"
