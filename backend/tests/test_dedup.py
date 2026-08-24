from app.dedup import find_duplicate, normalize
from app.models import Job, JobStatus
from app.schemas import JobListing


def test_normalize_strips_company_suffix_and_punctuation():
    assert normalize("Acme, Inc.") == "acme"
    assert normalize("Acme LLC") == "acme"
    assert normalize("  Full Stack Developer!! ") == "full stack developer"


def _make_job(db_session, **overrides) -> Job:
    defaults = dict(
        source="greenhouse",
        source_url="https://example.com/job/1",
        title="Full Stack Developer",
        company="Acme Inc",
        location="Tampa, FL",
        description="Build things.",
        status=JobStatus.DISCOVERED,
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_find_duplicate_matches_near_identical_title(db_session):
    _make_job(db_session)
    listing = JobListing(
        source="linkedin",
        source_url="https://linkedin.com/jobs/2",
        title="Full-Stack Developer",
        company="Acme, Inc.",
        location="Tampa, FL",
        description="Build things.",
    )
    assert find_duplicate(db_session, listing) is not None


def test_find_duplicate_returns_none_for_different_company(db_session):
    _make_job(db_session)
    listing = JobListing(
        source="linkedin",
        source_url="https://linkedin.com/jobs/2",
        title="Full Stack Developer",
        company="Globex Corp",
        location="Tampa, FL",
        description="Build things.",
    )
    assert find_duplicate(db_session, listing) is None


def test_find_duplicate_returns_none_for_different_location(db_session):
    _make_job(db_session)
    listing = JobListing(
        source="linkedin",
        source_url="https://linkedin.com/jobs/2",
        title="Full Stack Developer",
        company="Acme Inc",
        location="Remote",
        description="Build things.",
    )
    assert find_duplicate(db_session, listing) is None
