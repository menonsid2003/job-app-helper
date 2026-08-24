from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Base, get_db
from app.main import app


def _make_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_put_criteria_persists_and_returns_updated_config(db_session, tmp_path: Path, monkeypatch):
    criteria_path = tmp_path / "criteria.yaml"
    criteria_path.write_text("target_roles: []\n", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.criteria_config_path", criteria_path)
    monkeypatch.setattr("app.criteria.settings.criteria_config_path", criteria_path)

    client = next(_make_client(db_session))

    payload = {
        "target_roles": ["Data Engineer"],
        "exclude_keywords": ["intern"],
        "target_companies": {"greenhouse": ["stripe"]},
    }
    response = client.put("/api/criteria", json=payload)

    assert response.status_code == 200
    assert response.json()["target_roles"] == ["Data Engineer"]
    assert "Data Engineer" in criteria_path.read_text(encoding="utf-8")

    # A GET right after should reflect the saved file.
    get_response = client.get("/api/criteria")
    assert get_response.json()["target_roles"] == ["Data Engineer"]

    app.dependency_overrides.clear()
