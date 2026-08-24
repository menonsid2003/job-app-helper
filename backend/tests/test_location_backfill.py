import pytest

from app.connectors.base import Connector
from app.location_backfill import backfill_locations
from app.models import Job, JobStatus
from app.schemas import CriteriaConfig, JobListing, JobSpyCriteria


class FakeConnector(Connector):
    name = "fake"

    def __init__(self, listings: list[JobListing]):
        self._listings = listings

    def search(self, criteria, on_progress=None):
        return self._listings


def _job(source: str, url: str, location: str = "", is_remote=None, description: str = "x") -> Job:
    return Job(
        source=source, source_url=url, title="Data Engineer", company="Acme",
        location=location, is_remote=is_remote, description=description, status=JobStatus.SCORED,
    )


@pytest.fixture(autouse=True)
def _patch_criteria(monkeypatch):
    monkeypatch.setattr(
        "app.location_backfill.load_criteria",
        lambda: CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=True)),
    )


def test_backfill_fills_in_greenhouse_job_from_fresh_board_data(db_session):
    job = _job("greenhouse", "https://boards.greenhouse.io/acme/jobs/1")
    db_session.add(job)
    db_session.commit()

    fresh = FakeConnector([
        JobListing(
            source="greenhouse", source_url=job.source_url, title="Data Engineer", company="Acme",
            location="Austin, TX", description="x",
        )
    ])

    updated, not_found, skipped = backfill_locations(
        db_session, board_connectors={"greenhouse": fresh, "lever": FakeConnector([]), "workday": FakeConnector([])}
    )

    assert (updated, not_found, skipped) == (1, 0, 0)
    db_session.refresh(job)
    assert job.location == "Austin, TX"
    assert job.is_remote is False


def test_backfill_counts_not_found_when_posting_no_longer_listed(db_session):
    job = _job("greenhouse", "https://boards.greenhouse.io/acme/jobs/1")
    db_session.add(job)
    db_session.commit()

    updated, not_found, skipped = backfill_locations(
        db_session,
        board_connectors={"greenhouse": FakeConnector([]), "lever": FakeConnector([]), "workday": FakeConnector([])},
    )

    assert (updated, not_found, skipped) == (0, 1, 0)
    db_session.refresh(job)
    assert job.location == ""


def test_backfill_attempts_jobspy_family_jobs_via_jobspy_search(db_session):
    # A "linkedin"-sourced job (one of jobspy's per-board source tags, see
    # JobSpyConnector._parse_dataframe) — not a BOARD_CONNECTORS key, so it
    # must go through the jobspy re-search path, not be skipped.
    job = _job("linkedin", "https://linkedin.com/jobs/view/123")
    db_session.add(job)
    db_session.commit()

    fresh_jobspy = FakeConnector([
        JobListing(
            source="linkedin", source_url=job.source_url, title="Data Engineer", company="Acme",
            location="Remote", description="x",
        )
    ])

    updated, not_found, skipped = backfill_locations(
        db_session,
        board_connectors={"greenhouse": FakeConnector([]), "lever": FakeConnector([]), "workday": FakeConnector([])},
        jobspy_connector=fresh_jobspy,
    )

    assert (updated, not_found, skipped) == (1, 0, 0)
    db_session.refresh(job)
    assert job.location == ""
    assert job.is_remote is True


def test_backfill_skips_jobspy_family_jobs_when_jobspy_disabled(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.location_backfill.load_criteria",
        lambda: CriteriaConfig(target_roles=["Data Engineer"], jobspy=JobSpyCriteria(enabled=False)),
    )
    job = _job("indeed", "https://indeed.com/viewjob?jk=abc")
    db_session.add(job)
    db_session.commit()

    called = []

    class ExplodingConnector(Connector):
        name = "jobspy"

        def search(self, criteria, on_progress=None):
            called.append(True)
            raise AssertionError("should never be called when jobspy is disabled")

    updated, not_found, skipped = backfill_locations(
        db_session,
        board_connectors={"greenhouse": FakeConnector([]), "lever": FakeConnector([]), "workday": FakeConnector([])},
        jobspy_connector=ExplodingConnector(),
    )

    assert called == []
    # skipped=1 because no re-fetch was attempted; not_found=1 too, because
    # the free description-scan pass still runs regardless and (with this
    # job's plain "x" description) doesn't find anything either.
    assert (updated, not_found, skipped) == (0, 1, 1)
    db_session.refresh(job)
    assert job.location == ""


def test_backfill_falls_back_to_description_scan_when_refetch_finds_nothing(db_session):
    # Fresh re-fetch (board AND jobspy) turns up nothing for this job, but
    # its already-stored description mentions a city/state — the free scan
    # pass should catch it without needing any of the connectors to help.
    job = _job(
        "greenhouse", "https://boards.greenhouse.io/acme/jobs/1",
        description="Join our team! This role is based in our Denver, CO office and...",
    )
    db_session.add(job)
    db_session.commit()

    updated, not_found, skipped = backfill_locations(
        db_session,
        board_connectors={"greenhouse": FakeConnector([]), "lever": FakeConnector([]), "workday": FakeConnector([])},
    )

    assert (updated, not_found, skipped) == (1, 0, 0)
    db_session.refresh(job)
    assert job.location == "Denver, CO"
    assert job.is_remote is None  # description scan never touches is_remote


def test_backfill_prefers_refetch_over_description_scan(db_session):
    # When the re-fetch DOES find something, the description scan (which
    # would find a different city here) shouldn't get a second chance to
    # override it.
    job = _job(
        "greenhouse", "https://boards.greenhouse.io/acme/jobs/1",
        description="This role can also support our Denver, CO office.",
    )
    db_session.add(job)
    db_session.commit()

    fresh = FakeConnector([
        JobListing(
            source="greenhouse", source_url=job.source_url, title="Data Engineer", company="Acme",
            location="Austin, TX", description="x",
        )
    ])

    updated, not_found, skipped = backfill_locations(
        db_session, board_connectors={"greenhouse": fresh, "lever": FakeConnector([]), "workday": FakeConnector([])}
    )

    assert (updated, not_found, skipped) == (1, 0, 0)
    db_session.refresh(job)
    assert job.location == "Austin, TX"


def test_backfill_only_touches_jobs_with_blank_location(db_session):
    already_set = _job("greenhouse", "https://boards.greenhouse.io/acme/jobs/2", location="Denver, CO")
    db_session.add(already_set)
    db_session.commit()

    updated, not_found, skipped = backfill_locations(
        db_session,
        board_connectors={"greenhouse": FakeConnector([]), "lever": FakeConnector([]), "workday": FakeConnector([])},
    )

    assert (updated, not_found, skipped) == (0, 0, 0)
