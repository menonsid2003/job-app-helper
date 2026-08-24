from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.criteria import get_or_create_current_version, load_criteria, save_criteria
from app.db import get_db
from app.schemas import CriteriaConfig

router = APIRouter(prefix="/api/criteria", tags=["criteria"])


@router.get("", response_model=CriteriaConfig)
def get_criteria() -> CriteriaConfig:
    try:
        return load_criteria()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("", response_model=CriteriaConfig)
def update_criteria(criteria: CriteriaConfig, db: Session = Depends(get_db)) -> CriteriaConfig:
    save_criteria(criteria)
    # Record the new version immediately so it's traceable to when the edit
    # happened, rather than waiting for the next pipeline run to notice.
    get_or_create_current_version(db)
    return criteria
