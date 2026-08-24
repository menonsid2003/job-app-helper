from app.scoring.prefilter import (
    has_us_signal,
    is_relevant,
    matches_hard_exclude_keyword,
    matches_non_us_location,
)


def test_matches_hard_exclude_keyword_finds_case_insensitive_hit():
    description = "This role requires an ACTIVE Security Clearance Required for access."
    hit = matches_hard_exclude_keyword(description, ["security clearance required"])
    assert hit == "security clearance required"


def test_matches_hard_exclude_keyword_returns_none_when_no_hit():
    description = "We welcome candidates who need visa sponsorship."
    assert matches_hard_exclude_keyword(description, ["will not sponsor", "ts/sci"]) is None


def test_is_relevant_matches_on_title():
    assert is_relevant(
        title="Senior Data Engineer",
        description="Build ETL pipelines.",
        target_roles=["Data Engineer"],
        must_have_keywords=[],
        exclude_keywords=[],
    )


def test_is_relevant_matches_title_variant_via_shared_role_noun():
    # "Backend Engineer" isn't a verbatim target role, but shares the "engineer"
    # head noun with "Software Engineer" / "Data Engineer" — should still pass.
    assert is_relevant(
        title="Senior Backend Engineer, Platform Readiness",
        description="Own backend services.",
        target_roles=["Software Engineer", "Data Engineer"],
        must_have_keywords=[],
        exclude_keywords=[],
    )


def test_is_relevant_rejects_unrelated_role_even_if_description_mentions_tech_keywords():
    # Regression test: shared company boilerplate mentioning tech keywords
    # (e.g. AWS/Python in a "why work here" blurb) must not open the gate
    # for a completely unrelated role like HR.
    assert not is_relevant(
        title="Senior Team Member Relations Partner",
        description="Standard boilerplate mentioning Python, AWS, and SQL as part of our company-wide tech stack overview.",
        target_roles=["Software Engineer", "Data Engineer"],
        must_have_keywords=[],
        exclude_keywords=[],
    )


def test_is_relevant_rejects_on_exclude_keyword():
    assert not is_relevant(
        title="Senior Staff Software Engineer",
        description="10+ years experience required.",
        target_roles=["Software Engineer"],
        must_have_keywords=[],
        exclude_keywords=["senior staff", "10+ years"],
    )


def test_is_relevant_rejects_intern_title():
    assert not is_relevant(
        title="Software Engineering Intern",
        description="Summer internship program.",
        target_roles=["Software Engineer"],
        must_have_keywords=[],
        exclude_keywords=["intern", "internship"],
    )


def test_is_relevant_does_not_falsely_reject_international_or_internal():
    # Regression: a naive substring match on "intern" would also match
    # "international"/"internal", which is exactly wrong for an exclude filter.
    assert is_relevant(
        title="Software Engineer, International Payments",
        description="Work with our internal platform team on international expansion.",
        target_roles=["Software Engineer"],
        must_have_keywords=[],
        exclude_keywords=["intern", "internship"],
    )


# ---- Location / US-only hard constraint ----


def test_has_us_signal_true_for_city_state_pattern():
    assert has_us_signal("Chicago, IL")
    # CA/GA are deliberately excluded from the abbreviation whitelist (see
    # Delaware/Argentina/Canada collision tests below), but the string is
    # still recognized via the unambiguous IL/NY codes.
    assert has_us_signal("Chicago, IL; Atlanta, GA; New York, NY")


def test_has_us_signal_false_for_lone_ambiguous_state_code():
    # CA is deliberately not in the abbreviation whitelist (collides with
    # Canada in real data, e.g. "CA-Toronto") — a lone "City, CA" now falls
    # through to the LLM instead of being confidently accepted, which is the
    # safer failure direction for a hard constraint (extra LLM call, not a
    # false exclusion).
    assert not has_us_signal("San Francisco, CA")


def test_has_us_signal_true_for_explicit_us_remote():
    assert has_us_signal("Chicago, US-Remote")
    assert has_us_signal("US Remote")
    assert has_us_signal("CHI, ATL, US-REM")


def test_has_us_signal_false_for_canada_province_prefix_pattern():
    # Regression case from real Greenhouse data: "CA-" prefix means Canada
    # (province code), not the US state of California — must not collide
    # with the "City, CA" pattern that does mean California.
    assert not has_us_signal("CA-Toronto, CA-Montreal, CA-Vancouver")


def test_has_us_signal_false_for_bare_remote():
    assert not has_us_signal("Remote")
    assert not has_us_signal("")


def test_has_us_signal_true_for_dotted_us_followed_by_word():
    # Regression: "U.S." ends on a period, so a naive trailing \b fails when
    # followed by a space + word (both sides of that position are non-word
    # chars) — this broke "U.S. Remote" specifically.
    assert has_us_signal("CA Remote (BC & ON only); U.S. Remote")
    assert has_us_signal("U.S. Remote")
    assert has_us_signal("Remote, U.S.")


def test_has_us_signal_false_for_delaware_germany_collision():
    # Regression: real Greenhouse data uses "Berlin, DE" — DE must not be
    # read as the Delaware abbreviation.
    assert not has_us_signal("Berlin, DE")


def test_has_us_signal_false_for_arkansas_argentina_collision():
    # Regression: real Greenhouse data uses "Buenos Aires, AR".
    assert not has_us_signal("Buenos Aires, AR")


def test_matches_non_us_location_catches_berlin_de():
    assert matches_non_us_location("Berlin, DE", ["berlin"]) == "berlin"


def test_matches_non_us_location_catches_buenos_aires_ar():
    assert matches_non_us_location("Buenos Aires, AR", ["buenos aires"]) == "buenos aires"


def test_matches_non_us_location_catches_country_name():
    assert matches_non_us_location("Bengaluru, India", ["india"]) == "india"


def test_matches_non_us_location_catches_city_without_country():
    assert matches_non_us_location("Dublin", ["dublin", "ireland"]) == "dublin"


def test_matches_non_us_location_never_rejects_when_us_option_also_listed():
    # Regression case: a US-based candidate is eligible via the US-Remote
    # option even though Dublin is also listed.
    assert matches_non_us_location("Dublin, US-Remote", ["dublin", "ireland"]) is None


def test_matches_non_us_location_none_for_bare_remote():
    # Ambiguous — no country signal either way — left for the LLM to judge.
    assert matches_non_us_location("Remote", ["india", "canada"]) is None


def test_matches_non_us_location_none_for_empty_location():
    assert matches_non_us_location("", ["india", "canada"]) is None
