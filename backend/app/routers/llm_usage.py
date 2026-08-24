from fastapi import APIRouter

from app.schemas import ScoringUsageOut
from app.scoring_usage import scoring_usage

router = APIRouter(prefix="/api", tags=["llm-usage"])


@router.get("/llm-usage", response_model=ScoringUsageOut)
def get_scoring_usage() -> ScoringUsageOut:
    """Cumulative Score + Tailor token usage and estimated cost, independent
    of whether a pipeline run or tailor request is currently in flight —
    see app/scoring_usage.py."""
    return ScoringUsageOut(**scoring_usage.snapshot())
