from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CriteriaConfigVersion
from app.schemas import CriteriaConfig


def load_criteria(path: Path | None = None) -> CriteriaConfig:
    path = path or settings.criteria_config_path
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return CriteriaConfig.model_validate(data)


def load_criteria_yaml_text(path: Path | None = None) -> str:
    path = path or settings.criteria_config_path
    return path.read_text(encoding="utf-8")


def save_criteria(criteria: CriteriaConfig, path: Path | None = None) -> None:
    path = path or settings.criteria_config_path
    yaml_text = yaml.safe_dump(criteria.model_dump(mode="json"), sort_keys=False, default_flow_style=False)
    path.write_text(yaml_text, encoding="utf-8")


def get_or_create_current_version(db: Session, path: Path | None = None) -> CriteriaConfigVersion:
    """Return the CriteriaConfigVersion matching the current criteria.yaml contents,
    inserting a new version row if the file has changed since the last recorded one."""
    yaml_text = load_criteria_yaml_text(path)

    latest = db.execute(
        select(CriteriaConfigVersion).order_by(CriteriaConfigVersion.id.desc()).limit(1)
    ).scalar_one_or_none()

    if latest is not None and latest.yaml_blob == yaml_text:
        return latest

    version = CriteriaConfigVersion(yaml_blob=yaml_text)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version
