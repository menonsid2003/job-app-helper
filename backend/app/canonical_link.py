import logging
import re
from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz

from app.html_utils import strip_html

logger = logging.getLogger(__name__)

TITLE_MATCH_THRESHOLD = 90


@dataclass
class CanonicalPosting:
    url: str
    description: str

_SUFFIXES = re.compile(r"\b(inc|incorporated|llc|corp|corporation|co|ltd|limited|company)\b\.?", re.IGNORECASE)
_NON_ALNUM_SPACE = re.compile(r"[^a-z0-9\s]+")


def slugify_candidates(company: str) -> list[str]:
    """Candidate URL slugs for a company name, most-likely-first. Greenhouse/
    Lever board tokens are usually just the lowercased company name, hyphens
    or nothing between words — e.g. "Red Hat" -> ["red-hat", "redhat"]."""
    cleaned = _SUFFIXES.sub("", company.lower())
    cleaned = _NON_ALNUM_SPACE.sub(" ", cleaned).strip()
    words = cleaned.split()
    if not words:
        return []
    hyphenated = "-".join(words)
    joined = "".join(words)
    candidates = [hyphenated]
    if joined != hyphenated:
        candidates.append(joined)
    return candidates


def resolve_canonical_link(
    company: str,
    title: str,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """URL-only convenience wrapper around resolve_canonical_posting, kept
    for callers that only care about the link (e.g. the post-scoring
    canonical-link step in pipeline.py, which fetches the URL alone since it
    already has a good description by that point)."""
    match = resolve_canonical_posting(company, title, timeout_seconds=timeout_seconds, transport=transport)
    return match.url if match else None


def resolve_canonical_posting(
    company: str,
    title: str,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> CanonicalPosting | None:
    """Best-effort: probe Greenhouse's and Lever's public APIs for a board
    matching this company name, and look for a posting whose title fuzzy-
    matches — returning both its URL and full description. No search API
    involved — these platforms' board tokens are predictable from the
    company name, so this is a handful of GET requests, not scraping.
    Returns None on no confident match; never raises — a failure here
    should never block the pipeline."""
    candidates = slugify_candidates(company)
    if not candidates:
        return None

    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
            for slug in candidates:
                match = _try_greenhouse(client, slug, title)
                if match:
                    return match
            for slug in candidates:
                match = _try_lever(client, slug, title)
                if match:
                    return match
    except httpx.HTTPError as exc:
        logger.warning("Canonical link resolution failed for '%s': %s", company, exc)
        return None

    return None


def _best_title_posting(postings: list[dict], title: str, title_key: str) -> dict | None:
    best_score = 0.0
    best_posting = None
    for posting in postings:
        # token_set_ratio (not token_sort_ratio) — forgiving of extra
        # qualifier words like "Senior"/"Staff" that don't change the role,
        # which matters a lot here since we're confirming the *same* posting
        # re-fetched from a different endpoint, not screening for relevance.
        score = fuzz.token_set_ratio(title.lower(), str(posting.get(title_key, "")).lower())
        if score > best_score:
            best_score = score
            best_posting = posting
    if best_score >= TITLE_MATCH_THRESHOLD:
        return best_posting
    return None


def _try_greenhouse(client: httpx.Client, slug: str, title: str) -> CanonicalPosting | None:
    try:
        response = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    except httpx.TransportError:
        return None
    if response.status_code != 200:
        return None
    jobs = response.json().get("jobs", [])
    posting = _best_title_posting(jobs, title, "title")
    if not posting or not posting.get("absolute_url"):
        return None
    return CanonicalPosting(url=posting["absolute_url"], description=strip_html(posting.get("content", "")))


def _try_lever(client: httpx.Client, slug: str, title: str) -> CanonicalPosting | None:
    try:
        response = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    except httpx.TransportError:
        return None
    if response.status_code != 200:
        return None
    postings = response.json()
    if not isinstance(postings, list):
        return None
    posting = _best_title_posting(postings, title, "text")
    if not posting or not posting.get("hostedUrl"):
        return None

    description_parts = [posting.get("descriptionPlain", "")]
    for section in posting.get("lists") or []:
        heading = section.get("text", "")
        body = strip_html(section.get("content", ""))
        if heading or body:
            description_parts.append(f"{heading}\n{body}")
    additional = posting.get("additionalPlain", "")
    if additional:
        description_parts.append(additional)

    return CanonicalPosting(url=posting["hostedUrl"], description="\n\n".join(p for p in description_parts if p))
