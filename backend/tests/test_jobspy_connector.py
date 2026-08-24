import pandas as pd

from app.connectors.jobspy_connector import JobSpyConnector
from app.schemas import CriteriaConfig, JobSpyCriteria


def _fake_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _row(**overrides) -> dict:
    # Mirrors what jobspy's own JobPost.dict() actually produces: a single
    # "location" string (already formatted by its Location.display_location()),
    # never separate "city"/"state" columns — see jobspy/__init__.py and
    # jobspy/model.py. A fixture with city/state keys would hide the bug
    # where _format_location read fields that don't exist in real data.
    row = {
        "title": "Data Engineer",
        "company": "Acme",
        "job_url": "https://indeed.com/viewjob?jk=abc123",
        "job_url_direct": None,
        "location": "Tampa, FL",
        "is_remote": False,
        "min_amount": 90000,
        "max_amount": 120000,
        "currency": "USD",
        "interval": "yearly",
        "description": "Build data pipelines.",
        "date_posted": "2026-08-20",
    }
    row.update(overrides)
    return row


def test_jobspy_connector_disabled_by_default_returns_nothing():
    connector = JobSpyConnector(scrape_fn=lambda **kwargs: _fake_df([_row()]))
    criteria = CriteriaConfig(target_roles=["Data Engineer"], locations=["Tampa, FL"])

    listings = connector.search(criteria)

    assert listings == []


def test_jobspy_connector_parses_listings_when_enabled():
    calls = []

    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return _fake_df([_row()])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(
        target_roles=["Data Engineer"],
        locations=["Tampa, FL"],
        jobspy=JobSpyCriteria(enabled=True, sites=["indeed"], results_wanted=10, hours_old=48, country_indeed="USA"),
    )

    listings = connector.search(criteria)

    assert len(listings) == 1
    job = listings[0]
    assert job.source == "jobspy"
    assert job.title == "Data Engineer"
    assert job.company == "Acme"
    assert job.location == "Tampa, FL"
    assert job.salary_text == "USD 90,000-120,000/yearly"
    assert job.source_url == "https://indeed.com/viewjob?jk=abc123"

    assert len(calls) == 1
    assert calls[0]["site_name"] == ["indeed"]
    assert calls[0]["search_term"] == "Data Engineer"
    assert calls[0]["location"] == "Tampa, FL"
    assert calls[0]["results_wanted"] == 10
    assert calls[0]["hours_old"] == 48
    assert calls[0]["country_indeed"] == "USA"


def test_jobspy_connector_uses_the_actual_board_as_source_when_present():
    connector = JobSpyConnector(scrape_fn=lambda **kwargs: _fake_df([_row(site="linkedin")]))
    criteria = CriteriaConfig(
        target_roles=["Data Engineer"], locations=["Tampa, FL"],
        jobspy=JobSpyCriteria(enabled=True),
    )

    listings = connector.search(criteria)

    assert listings[0].source == "linkedin"


def test_jobspy_connector_falls_back_to_jobspy_when_site_column_missing():
    connector = JobSpyConnector(scrape_fn=lambda **kwargs: _fake_df([_row()]))
    criteria = CriteriaConfig(
        target_roles=["Data Engineer"], locations=["Tampa, FL"],
        jobspy=JobSpyCriteria(enabled=True),
    )

    listings = connector.search(criteria)

    assert listings[0].source == "jobspy"


def test_jobspy_connector_searches_every_role_by_location_combination():
    calls = []

    def fake_scrape(**kwargs):
        calls.append((kwargs["search_term"], kwargs["location"]))
        return _fake_df([])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(
        target_roles=["Data Engineer", "Software Engineer"],
        locations=["Tampa, FL", "Remote"],
        jobspy=JobSpyCriteria(enabled=True),
    )

    connector.search(criteria)

    assert set(calls) == {
        ("Data Engineer", "Tampa, FL"),
        ("Data Engineer", "Remote"),
        ("Software Engineer", "Tampa, FL"),
        ("Software Engineer", "Remote"),
    }


def test_jobspy_connector_dedupes_by_url_across_searches():
    def fake_scrape(**kwargs):
        return _fake_df([_row(), _row()])  # same job_url both times

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(
        target_roles=["Data Engineer"],
        locations=["Tampa, FL", "Remote"],
        jobspy=JobSpyCriteria(enabled=True),
    )

    listings = connector.search(criteria)

    assert len(listings) == 1


def test_jobspy_connector_carries_remote_hint_when_no_location():
    def fake_scrape(**kwargs):
        return _fake_df([_row(location=None, is_remote=True)])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings[0].location == ""
    assert listings[0].is_remote_hint is True


def test_jobspy_connector_leaves_location_blank_when_absent_and_not_remote():
    def fake_scrape(**kwargs):
        return _fake_df([_row(location=None, is_remote=False)])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings[0].location == ""
    assert listings[0].is_remote_hint is False


def test_jobspy_connector_keeps_location_text_when_also_remote():
    # The bug this guards against: a listing with BOTH a real location and
    # is_remote=True used to collapse to the bare string "Remote", losing
    # the location entirely. Now location and remote status are carried
    # separately, so neither destroys the other.
    def fake_scrape(**kwargs):
        return _fake_df([_row(location="San Francisco, CA", is_remote=True)])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings[0].location == "San Francisco, CA"
    assert listings[0].is_remote_hint is True


def test_jobspy_connector_treats_na_placeholder_as_missing():
    # jobspy's LinkedIn scraper writes literal "N/A" (not None) for
    # title/company when it can't find the expected tag on the card.
    def fake_scrape(**kwargs):
        return _fake_df([_row(company="N/A", title="N/A")])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings[0].company == ""
    assert listings[0].title == ""


def test_jobspy_connector_handles_missing_salary():
    def fake_scrape(**kwargs):
        return _fake_df([_row(min_amount=None, max_amount=None)])

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings[0].salary_text is None


def test_jobspy_connector_survives_a_site_failure():
    def fake_scrape(**kwargs):
        raise RuntimeError("blocked by anti-bot")

    connector = JobSpyConnector(scrape_fn=fake_scrape)
    criteria = CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True))

    listings = connector.search(criteria)

    assert listings == []
