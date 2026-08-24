from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Job, JobStatus, Score


def _make_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _seed(db_session):
    scored_job = Job(
        source="greenhouse",
        source_url="https://example.com/1",
        title="Data Engineer",
        company="Acme",
        location="Remote",
        description="Build pipelines.",
        status=JobStatus.SCORED,
    )
    excluded_job = Job(
        source="greenhouse",
        source_url="https://example.com/2",
        title="Data Engineer (Clearance Required)",
        company="Acme",
        location="Remote",
        description="Requires active TS/SCI clearance.",
        status=JobStatus.EXCLUDED,
    )
    db_session.add_all([scored_job, excluded_job])
    db_session.commit()
    db_session.refresh(scored_job)
    db_session.refresh(excluded_job)

    db_session.add_all(
        [
            Score(
                job_id=scored_job.id,
                score=82,
                reasoning="Strong fit.",
                matched_keywords=["Python", "SQL"],
                missing_requirements=["Spark"],
                role_category="Data Engineer",
                red_flags=[],
                work_authorization={
                    "citizenship_required": False,
                    "security_clearance_required": False,
                    "sponsorship_mentioned": "not_mentioned",
                    "hard_exclude": False,
                },
                model_used="llama3.1:8b",
            ),
            Score(
                job_id=excluded_job.id,
                score=0,
                reasoning="Excluded by prefilter.",
                matched_keywords=[],
                missing_requirements=[],
                role_category="Other",
                red_flags=["ts/sci"],
                work_authorization={
                    "citizenship_required": False,
                    "security_clearance_required": True,
                    "sponsorship_mentioned": "not_mentioned",
                    "hard_exclude": True,
                },
                model_used="prefilter",
            ),
        ]
    )
    db_session.commit()
    return scored_job, excluded_job


def test_list_jobs_returns_scored_job_with_nested_score(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1
    job = data[0]
    assert job["id"] == scored_job.id
    assert job["status"] == "scored"
    assert job["latest_score"]["score"] == 82
    assert job["latest_score"]["matched_keywords"] == ["Python", "SQL"]
    assert job["latest_score"]["work_authorization"]["hard_exclude"] is False

    app.dependency_overrides.clear()


def test_list_jobs_excludes_excluded_status_by_default(db_session):
    _seed(db_session)
    client = next(_make_client(db_session))

    response = client.get("/api/jobs")
    companies_statuses = [j["status"] for j in response.json()]
    assert "excluded" not in companies_statuses

    app.dependency_overrides.clear()


def test_excluded_endpoint_returns_pipeline_excluded_jobs(db_session):
    _, excluded_job = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.get("/api/jobs/excluded")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1
    assert data[0]["id"] == excluded_job.id
    assert data[0]["latest_score"]["work_authorization"]["hard_exclude"] is True


def test_excluded_endpoint_also_returns_user_skipped_jobs(db_session):
    """A job removed by hand (Jobs listing "Exclude", or Tracking Table
    "Remove", both of which set status=skip) shows up here too, so
    dismissing something always has a visible, recoverable home."""
    scored_job, excluded_job = _seed(db_session)
    scored_job.status = JobStatus.SKIP
    db_session.commit()
    client = next(_make_client(db_session))

    response = client.get("/api/jobs/excluded")
    ids = {j["id"] for j in response.json()}

    assert ids == {scored_job.id, excluded_job.id}

    app.dependency_overrides.clear()


def test_get_job_detail_includes_description(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.get(f"/api/jobs/{scored_job.id}")
    assert response.status_code == 200
    assert response.json()["description"] == "Build pipelines."

    app.dependency_overrides.clear()


def test_get_job_detail_404_for_missing_job(db_session):
    client = next(_make_client(db_session))
    response = client.get("/api/jobs/9999")
    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_patch_job_updates_status_and_notes(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.patch(f"/api/jobs/{scored_job.id}", json={"status": "pursue", "notes": "Applied via referral"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pursue"
    assert data["notes"] == "Applied via referral"

    app.dependency_overrides.clear()


def test_patch_job_partial_update_leaves_other_field_unchanged(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"notes": "first note"})
    response = client.patch(f"/api/jobs/{scored_job.id}", json={"status": "pursue"})

    assert response.json()["notes"] == "first note"
    assert response.json()["status"] == "pursue"

    app.dependency_overrides.clear()


def test_patch_job_404_for_missing_job(db_session):
    client = next(_make_client(db_session))
    response = client.patch("/api/jobs/9999", json={"status": "pursue"})
    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_patch_job_rejects_invalid_status(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.patch(f"/api/jobs/{scored_job.id}", json={"status": "not_a_real_status"})

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_patch_job_updates_company_and_location(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.patch(
        f"/api/jobs/{scored_job.id}", json={"company": "Acme Corp", "location": "Austin, TX"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["company"] == "Acme Corp"
    assert data["location"] == "Austin, TX"

    app.dependency_overrides.clear()


def test_patch_job_company_and_location_partial_update_leaves_other_fields_unchanged(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"status": "pursue", "notes": "keep me"})
    response = client.patch(f"/api/jobs/{scored_job.id}", json={"company": "Acme Corp"})

    data = response.json()
    assert data["company"] == "Acme Corp"
    assert data["status"] == "pursue"
    assert data["notes"] == "keep me"

    app.dependency_overrides.clear()


def test_patch_job_can_set_is_remote_true_and_false(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    response = client.patch(f"/api/jobs/{scored_job.id}", json={"is_remote": True})
    assert response.json()["is_remote"] is True

    response = client.patch(f"/api/jobs/{scored_job.id}", json={"is_remote": False})
    assert response.json()["is_remote"] is False

    app.dependency_overrides.clear()


def test_patch_job_can_explicitly_reset_is_remote_to_unknown(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"is_remote": True})
    response = client.patch(f"/api/jobs/{scored_job.id}", json={"is_remote": None})

    assert response.json()["is_remote"] is None

    app.dependency_overrides.clear()


def test_patch_job_omitting_is_remote_leaves_it_unchanged(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"is_remote": True})
    response = client.patch(f"/api/jobs/{scored_job.id}", json={"company": "Acme Corp"})

    assert response.json()["is_remote"] is True

    app.dependency_overrides.clear()


def test_pursued_job_disappears_from_default_jobs_view(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"status": "pursue"})
    response = client.get("/api/jobs")

    assert scored_job.id not in [j["id"] for j in response.json()]

    app.dependency_overrides.clear()


def test_tracking_endpoint_returns_pursued_job(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"status": "pursue"})
    response = client.get("/api/jobs/tracking")

    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert scored_job.id in ids


def test_tracking_endpoint_excludes_skipped_job(db_session):
    scored_job, _ = _seed(db_session)
    client = next(_make_client(db_session))

    client.patch(f"/api/jobs/{scored_job.id}", json={"status": "skip"})
    response = client.get("/api/jobs/tracking")

    assert scored_job.id not in [j["id"] for j in response.json()]
