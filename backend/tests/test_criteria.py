from pathlib import Path

from app.criteria import get_or_create_current_version, load_criteria, save_criteria
from app.schemas import CriteriaConfig


def test_save_and_load_criteria_round_trips(tmp_path: Path):
    path = tmp_path / "criteria.yaml"
    original = CriteriaConfig(
        target_roles=["Data Engineer"],
        exclude_keywords=["intern"],
        target_companies={"greenhouse": ["stripe"], "lever": ["palantir"]},
        prefer_full_time=False,
    )

    save_criteria(original, path=path)
    loaded = load_criteria(path=path)

    assert loaded == original


def test_get_or_create_current_version_creates_row_on_first_call(db_session, tmp_path: Path):
    path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(), path=path)

    version = get_or_create_current_version(db_session, path=path)

    assert version.id is not None
    assert "target_roles" in version.yaml_blob


def test_get_or_create_current_version_reuses_row_when_unchanged(db_session, tmp_path: Path):
    path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(), path=path)

    first = get_or_create_current_version(db_session, path=path)
    second = get_or_create_current_version(db_session, path=path)

    assert first.id == second.id


def test_get_or_create_current_version_creates_new_row_after_edit(db_session, tmp_path: Path):
    path = tmp_path / "criteria.yaml"
    save_criteria(CriteriaConfig(target_roles=["Data Engineer"]), path=path)
    first = get_or_create_current_version(db_session, path=path)

    save_criteria(CriteriaConfig(target_roles=["Software Engineer"]), path=path)
    second = get_or_create_current_version(db_session, path=path)

    assert second.id != first.id
