import re


def title_matches_target_role(title: str, target_roles: list[str]) -> bool:
    """Public wrapper around the title/role-noun match, for connectors that
    need to pre-filter by title before an expensive per-job detail fetch
    (e.g. Workday, where listing search and job detail are separate calls)."""
    if not target_roles:
        return True
    head_words = role_head_words(target_roles)
    lowered_title = title.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered_title) for word in head_words)


def role_head_words(target_roles: list[str]) -> set[str]:
    """The head noun of each target role (e.g. "engineer" from "Software Engineer",
    "developer" from "ServiceNow Developer") — used as a cheap, config-derived
    signal for whether a title is even in the right ballpark."""
    words = set()
    for role in target_roles:
        parts = role.split()
        if parts:
            words.add(parts[-1].lower())
    return words


def matches_hard_exclude_keyword(description: str, keywords: list[str]) -> str | None:
    """Cheap pre-filter over the raw description, run before spending an LLM call.
    Returns the first matching keyword, or None if nothing hit."""
    lowered = description.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def is_relevant(
    title: str,
    description: str,
    target_roles: list[str],
    must_have_keywords: list[str],
    exclude_keywords: list[str],
) -> bool:
    """Lightweight relevance check so we don't burn an LLM call on obviously
    irrelevant postings. Not a substitute for LLM scoring — just a cheap gate.
    Deliberately does not gate on nice_to_have_keywords: those only influence
    LLM scoring, since gating on them lets shared company boilerplate (which
    often mentions common tech keywords) open the gate for unrelated roles
    like HR or Sales. Location/country eligibility is handled separately (see
    matches_non_us_location) since it's a hard constraint, not a soft
    relevance signal — this function is about role fit only."""
    lowered_title = title.lower()
    lowered_desc = description.lower()

    for keyword in exclude_keywords:
        # Word-boundary match, not substring — a naive substring check on a
        # short keyword like "intern" would also match "international" or
        # "internal", which is exactly wrong for an exclude filter.
        pattern = rf"\b{re.escape(keyword.lower())}\b"
        if re.search(pattern, lowered_title) or re.search(pattern, lowered_desc):
            return False

    if must_have_keywords:
        if not any(kw.lower() in lowered_desc for kw in must_have_keywords):
            return False

    if not title_matches_target_role(title, target_roles):
        return False

    return True


# US state abbreviations (incl. DC), matched only in a "City, ST" style
# pattern (comma immediately before the code) to avoid colliding with
# non-US patterns that also happen to use two-letter codes. Deliberately
# EXCLUDES abbreviations that collide with real non-US codes seen in actual
# Greenhouse data: CA (California vs. Canada, e.g. "CA-Toronto"), DE
# (Delaware vs. Germany, e.g. "Berlin, DE"), AR (Arkansas vs. Argentina,
# e.g. "Buenos Aires, AR"), IN (Indiana vs. India), CO (Colorado vs.
# Colombia), GA (Georgia the state vs. Georgia the country), MA
# (Massachusetts vs. Morocco), PA (Pennsylvania vs. Panama). Those states
# still match via their full name (_US_FULL_STATE_PATTERN below); dropping
# the abbreviation for them just means a lone "San Francisco, CA" falls
# through to the LLM instead of being resolved by the cheap check — safer
# than risking a false exclusion.
_US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "CT", "FL", "HI", "ID", "IL", "IA",
    "KS", "KY", "LA", "ME", "MD", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_STATE_ABBR_PATTERN = re.compile(r",\s*(" + "|".join(_US_STATE_ABBREVIATIONS) + r")\b\.?\s*(,|;|$)", re.IGNORECASE)

_US_FULL_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
]
_US_FULL_STATE_PATTERN = re.compile(r"\b(" + "|".join(_US_FULL_STATE_NAMES) + r")\b", re.IGNORECASE)

_US_SIGNAL_PATTERN = re.compile(
    # "u.s." ends on a period, so a trailing \b fails when it's followed by a
    # space + another word (e.g. "U.S. Remote") — both sides of that position
    # are non-word chars, so \b never fires. A lookahead for a separator
    # avoids that instead of requiring a word/non-word transition.
    r"\bu\.s\.a?\.?(?=[\s,;)\]/-]|$)"
    r"|\busa\b"
    r"|\bunited states\b"
    r"|\bus\b",
    re.IGNORECASE,
)


def has_us_signal(location: str) -> bool:
    """True if the location string contains an unambiguous US indicator.
    Used to avoid rejecting multi-option postings like "Dublin, US-Remote" —
    a US-based candidate is eligible via the US option even though another
    non-US city is also listed."""
    if not location:
        return False
    if _US_SIGNAL_PATTERN.search(location):
        return True
    if _US_STATE_ABBR_PATTERN.search(location):
        return True
    if _US_FULL_STATE_PATTERN.search(location):
        return True
    return False


def matches_non_us_location(location: str, exclude_location_keywords: list[str]) -> str | None:
    """Cheap pre-filter for the US-only hard constraint, run before spending
    an LLM call — mirrors matches_hard_exclude_keyword. If the location
    string contains an explicit US signal, it's never rejected here even if
    another (non-US) option is also listed. Otherwise, checks the location
    against a configurable blocklist of country/city/region names. A bare
    "Remote" or an unrecognized location with no signal either way is left
    for the LLM to judge from the full job description (see
    ScoreResult.location_us_eligible) rather than guessed here."""
    if not location or has_us_signal(location):
        return None
    lowered = location.lower()
    for keyword in exclude_location_keywords:
        if keyword.lower() in lowered:
            return keyword
    return None
