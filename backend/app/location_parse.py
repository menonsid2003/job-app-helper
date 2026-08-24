import re

# Word-boundary match — also fires inside "Remote-first"/"Remote/Hybrid" since
# "-" and "/" are non-word characters, so the boundary exists right after "e".
_REMOTE_SIGNAL_PATTERN = re.compile(r"\bremote\b", re.IGNORECASE)

# Strips "remote" plus any modifier word directly glued to it (fully/100%/
# mostly/primarily on one side, -first/-friendly/-only on the other), so
# cleaning "Fully Remote" or "Remote-First" doesn't leave an orphaned
# modifier word behind once "remote" itself is gone.
_REMOTE_PHRASE_PATTERN = re.compile(
    r"(100%\s*)?\b(fully|mostly|primarily)?\s*remote(-first|-friendly|-only)?\b",
    re.IGNORECASE,
)

# A comma/semicolon-joined location list ("US-Remote, US-San Francisco,
# US-Chicago") needs cleanup per-segment, not just at the string's outer
# edges — removing "Remote" from the first segment above leaves a dangling
# "US-" that only a per-segment edge-trim catches; a single trim of the
# whole string's start/end would leave it sitting untouched in the middle.
_SEGMENT_SPLIT = re.compile(r"[;,]")
_SEGMENT_EDGE_SEPARATORS = re.compile(r"^[\s\-–—/|·()]+|[\s\-–—/|·()]+$")
_EMPTY_PARENS = re.compile(r"\(\s*\)")

# A trailing/leading "US" segment on an otherwise-real location is pure
# redundancy for this app (criteria.country_only is US-only already) rather
# than real information — "Tampa, FL, US" and "Tampa,FL" should normalize to
# the same "Tampa, FL", not be treated as two different locations by the
# Jobs table's location filter/grouping.
_US_COUNTRY_TOKENS = {"us", "usa", "united states", "united states of america"}


def _is_us_country_token(segment: str) -> bool:
    # Strip periods before comparing so "U.S." / "U.S.A." match the same as
    # "US" / "USA" without needing every punctuation variant spelled out.
    return segment.lower().replace(".", "").strip() in _US_COUNTRY_TOKENS


def _clean_segments(text: str, drop_us_country_token: bool = True) -> str:
    """Split on commas/semicolons, trim each segment's edge punctuation, drop
    empty segments, and rejoin with a consistent ", " — this alone already
    makes "Tampa,FL" and "Tampa, FL" converge to the same string. When
    drop_us_country_token is set (the default), a bare "US"/"USA"/"United
    States" segment is also dropped, wherever it falls in the list — unless
    that would empty the list out entirely (a location that's *just*
    "US" keeps it rather than collapsing to nothing, which would look
    indistinguishable from never having found a location at all)."""
    all_segments = []
    kept_segments = []
    for raw_segment in _SEGMENT_SPLIT.split(text):
        segment = _SEGMENT_EDGE_SEPARATORS.sub("", raw_segment).strip()
        if not segment:
            continue
        all_segments.append(segment)
        if drop_us_country_token and _is_us_country_token(segment):
            continue
        kept_segments.append(segment)
    return ", ".join(kept_segments if kept_segments else all_segments)


# Full set of US state abbreviations, unlike the deliberately-narrowed one in
# app/scoring/prefilter.py — that one drops ambiguous codes (CA, DE, AR, IN,
# CO, GA, MA, PA) because a false match there means wrongly hard-excluding a
# real US job. Here a false match just means "found nothing, stays blank" —
# the miss is cheap, so the full set (including CA/PA, both far too common to
# leave out) is the right tradeoff for this purpose.
_US_STATE_ABBREVIATIONS_ALL = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}

# "City[, City...], ST" — one to three capitalized words immediately before a
# comma and a real state abbreviation (e.g. "Austin, TX", "San Francisco, CA",
# "Winston-Salem, NC"). Deliberately narrow: this scans free-form prose (a
# job description), not a structured field, so a loose pattern would risk
# pulling in a client's office city or an unrelated place-drop rather than
# the job's own location. A city name alone (no state) is never returned —
# there's no reliable way to tell a real city from any other capitalized
# noun phrase without a state anchor.
_CITY_STATE_IN_TEXT_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z.]+(?:[ -][A-Z][a-zA-Z.]+){0,2}),\s*(" + "|".join(sorted(_US_STATE_ABBREVIATIONS_ALL)) + r")\b"
)


def extract_location_from_text(text: str) -> str | None:
    """Best-effort scan of free-form text (a job description) for a
    "City, ST" pattern — for a listing whose own location field came back
    blank (and stayed blank after a fresh re-fetch attempt; see
    app/location_backfill.py) but that states its location in the body
    text somewhere. Returns the first match as "City, ST", or None. Only
    ever adds a location that wasn't there before — never used to override
    or contradict an existing one, since a description can legitimately
    mention other cities (a client site, a company HQ) that aren't where
    the role itself is based."""
    match = _CITY_STATE_IN_TEXT_PATTERN.search(text or "")
    if not match:
        return None
    return f"{match.group(1)}, {match.group(2)}"


def parse_remote_and_location(raw: str) -> tuple[bool | None, str]:
    """Split a raw connector location string into (is_remote, location_text).

    Connectors currently store the whole raw string (e.g. "Remote",
    "San Francisco, CA (Remote)", "Dublin, US-Remote") in one field, which
    makes a job that's simply remote-with-no-city-given indistinguishable at
    a glance from one where "Remote" swallowed real geography that was also
    present. This pulls the remote signal out into its own bool and leaves
    location_text as geography only.

    is_remote is True/False when the string says either way, None when the
    string is blank (no signal at all — distinct from "confirmed not
    remote"). location_text is "" when the only content was remote-related
    wording (a bare "Remote" or "Fully Remote" has no geography to keep).

    location_text is also normalized via _clean_segments regardless of
    remote status, so "Tampa,FL,US" and "Tampa, FL" converge to the same
    "Tampa, FL" — consistent spacing, and a redundant "US"/"USA"/"United
    States" segment dropped (this app is US-only per criteria.country_only,
    so that token never carries real information beyond what's already
    implied)."""
    raw = (raw or "").strip()
    if not raw:
        return None, ""

    is_remote = bool(_REMOTE_SIGNAL_PATTERN.search(raw))
    if not is_remote:
        return False, _clean_segments(raw)

    cleaned = _REMOTE_PHRASE_PATTERN.sub("", raw)
    cleaned = _EMPTY_PARENS.sub("", cleaned)
    cleaned = _clean_segments(cleaned)
    return True, cleaned
