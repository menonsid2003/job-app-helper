from app.location_parse import extract_location_from_text, parse_remote_and_location


def test_bare_remote_has_no_geography():
    assert parse_remote_and_location("Remote") == (True, "")


def test_fully_remote_has_no_geography():
    assert parse_remote_and_location("Fully Remote") == (True, "")


def test_hundred_percent_remote_has_no_geography():
    assert parse_remote_and_location("100% Remote") == (True, "")


def test_city_with_parenthetical_remote_keeps_city():
    assert parse_remote_and_location("San Francisco, CA (Remote)") == (True, "San Francisco, CA")


def test_remote_dash_city_keeps_city():
    assert parse_remote_and_location("Remote - New York, NY") == (True, "New York, NY")


def test_compound_us_remote_drops_redundant_country_keeps_city():
    # "US" used to be kept here as useful context, but per the normalization
    # goal (a bare "US" is redundant once real geography is present, same as
    # "Tampa, FL, US" -> "Tampa, FL"), it's now dropped consistently.
    assert parse_remote_and_location("Dublin, US-Remote") == (True, "Dublin")


def test_remote_first_strips_suffix_too():
    assert parse_remote_and_location("Remote-first") == (True, "")


def test_plain_city_is_not_remote():
    assert parse_remote_and_location("New York, NY") == (False, "New York, NY")


def test_blank_location_has_no_signal():
    assert parse_remote_and_location("") == (None, "")
    assert parse_remote_and_location("   ") == (None, "")


def test_whitespace_is_trimmed_on_non_remote_location():
    assert parse_remote_and_location("  Austin, TX  ") == (False, "Austin, TX")


def test_remote_removed_from_middle_of_compound_list_leaves_no_residue():
    # Real data seen in production: removing "Remote" from the first segment
    # of a longer list used to leave a dangling "US-," in front of the rest
    # ("US-, US-San Francisco, ...") because only the whole string's outer
    # edges were trimmed, not each comma-separated segment's own edges.
    # The bare "US" left over from the first segment is now also dropped as
    # redundant (real geography is present elsewhere in the list), on top of
    # the residue fix.
    raw = "US-Remote, US-San Francisco, US-Chicago, US-New York, US-Seattle, US-Texas"
    assert parse_remote_and_location(raw) == (
        True,
        "US-San Francisco, US-Chicago, US-New York, US-Seattle, US-Texas",
    )


def test_semicolon_comma_separators_collapse_cleanly():
    # Also seen in production: "A; , B; , C" style lists from sources that
    # join with "; " between a leading comma-prefixed item and the next.
    raw = "Bangalore, India; , Canada; , United Kingdom"
    assert parse_remote_and_location(raw) == (False, "Bangalore, India, Canada, United Kingdom")


def test_missing_space_after_comma_normalizes_the_same_as_spaced():
    assert parse_remote_and_location("Tampa,FL") == (False, "Tampa, FL")
    assert parse_remote_and_location("Tampa, FL") == (False, "Tampa, FL")


def test_trailing_us_country_token_is_dropped():
    assert parse_remote_and_location("Tampa,FL,US") == (False, "Tampa, FL")
    assert parse_remote_and_location("Tampa, FL, USA") == (False, "Tampa, FL")
    assert parse_remote_and_location("Tampa, FL, United States") == (False, "Tampa, FL")
    assert parse_remote_and_location("Tampa, FL, U.S.") == (False, "Tampa, FL")


def test_leading_us_country_token_is_also_dropped():
    # Seen in real data: "US, Chicago, Seattle, San Francisco" (country
    # listed first, not last).
    assert parse_remote_and_location("US, Chicago, Seattle") == (False, "Chicago, Seattle")


def test_bare_us_token_is_kept_rather_than_emptied():
    # Dropping the only segment would make this indistinguishable from a
    # location that was never found at all.
    assert parse_remote_and_location("US") == (False, "US")
    assert parse_remote_and_location("United States") == (False, "United States")


def test_us_token_survives_remote_wording_when_its_all_thats_left():
    # "US" is the only segment left once "Remote" is stripped out — kept
    # rather than emptied, same fallback as a bare "US" location on its own,
    # so "remote, US-scoped" still reads differently from "remote, no
    # geography at all".
    assert parse_remote_and_location("Remote, US") == (True, "US")


def test_us_token_dropped_alongside_remote_wording_when_real_geography_remains():
    assert parse_remote_and_location("Austin, TX, US (Remote)") == (True, "Austin, TX")


def test_extract_location_finds_city_state_in_prose():
    text = "We're a fast-growing startup. This role is based out of our Austin, TX office and reports to..."
    assert extract_location_from_text(text) == "Austin, TX"


def test_extract_location_handles_two_word_city():
    text = "Join our San Francisco, CA team building the next generation of..."
    assert extract_location_from_text(text) == "San Francisco, CA"


def test_extract_location_finds_california_and_pennsylvania():
    # These two are deliberately excluded from prefilter.py's narrower
    # US-signal set (CA collides with Canada, PA is ambiguous elsewhere) —
    # confirm this module uses the full set instead, since a miss here is
    # just "found nothing," not a false hard-exclude.
    assert extract_location_from_text("Los Angeles, CA is where our HQ is.") == "Los Angeles, CA"
    assert extract_location_from_text("Reporting to our Philadelphia, PA studio.") == "Philadelphia, PA"


def test_extract_location_returns_none_without_state_anchor():
    assert extract_location_from_text("We work with clients across Chicago and Denver regularly.") is None


def test_extract_location_returns_none_on_empty_text():
    assert extract_location_from_text("") is None
    assert extract_location_from_text(None) is None
