import re

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.location_parse import parse_remote_and_location
from app.models import Job
from app.schemas import JobListing

# MVP scale (hundreds-low thousands of jobs) — comparing in Python against
# normalized fields is simpler and more correct than an ILIKE prefilter,
# which breaks on punctuation differences (e.g. "Acme, Inc." vs "Acme Inc").

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|corp|corporation|co|ltd|limited|company)\b\.?", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

TITLE_MATCH_THRESHOLD = 90


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = _COMPANY_SUFFIXES.sub("", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def find_duplicate(db: Session, listing: JobListing) -> Job | None:
    """Return an existing Job that looks like the same posting, or None."""
    norm_company = normalize(listing.company)
    # Job.location is stored with remote-wording stripped out (see
    # app/location_parse.py), so the incoming listing's raw location is run
    # through the same parse before comparing — otherwise a bare "Remote"
    # listing would never fuzzy-match its own stored (blank) location on a
    # later scan and get re-discovered as a duplicate every run. The stored
    # side is parsed too rather than assumed already-clean — cheap, and a
    # no-op on data that already went through ingestion or the migration.
    _, cleaned_listing_location = parse_remote_and_location(listing.location)
    norm_location = normalize(cleaned_listing_location)
    norm_title = normalize(listing.title)

    candidates = db.execute(select(Job)).scalars().all()

    for candidate in candidates:
        if normalize(candidate.company) != norm_company:
            continue
        _, cleaned_candidate_location = parse_remote_and_location(candidate.location)
        # Fuzzy, not exact — a repost's location text can drift slightly
        # ("Tampa, FL" vs "Tampa, Florida", "Remote - US" vs "Remote, US")
        # without being a genuinely different posting. Two blank locations
        # still match each other (ratio 100), same as before.
        if fuzz.token_sort_ratio(norm_location, normalize(cleaned_candidate_location)) < TITLE_MATCH_THRESHOLD:
            continue
        if fuzz.token_sort_ratio(norm_title, normalize(candidate.title)) >= TITLE_MATCH_THRESHOLD:
            return candidate

    return None
