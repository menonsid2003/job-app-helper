import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.agent_apply_state import agent_apply_state
from app.agent_apply_usage import agent_apply_usage
from app.apply_agent import chrome
from app.apply_agent.orchestrator import run_agent_apply
from app.criteria import load_criteria
from app.models import Application, ApplicationStatus
from app.pipeline_state import RunStatus
from app.schemas import AgentApplyStatusOut, AgentApplyUsageOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-apply", tags=["agent-apply"])


@router.get("/status", response_model=AgentApplyStatusOut)
def get_agent_apply_status() -> AgentApplyStatusOut:
    return AgentApplyStatusOut(**agent_apply_state.snapshot())


@router.get("/usage", response_model=AgentApplyUsageOut)
def get_agent_apply_usage() -> AgentApplyUsageOut:
    """Cumulative token usage + rate-limit window status, independent of
    whether a run is currently active — see app/agent_apply_usage.py."""
    return AgentApplyUsageOut(**agent_apply_usage.snapshot())


@router.post("/stop", response_model=AgentApplyStatusOut)
def stop_agent_apply() -> AgentApplyStatusOut:
    agent_apply_state.request_stop()
    return AgentApplyStatusOut(**agent_apply_state.snapshot())


@router.post("/run", response_model=AgentApplyStatusOut, status_code=202)
def trigger_agent_apply(background_tasks: BackgroundTasks) -> AgentApplyStatusOut:
    if agent_apply_state.status == RunStatus.RUNNING:
        return AgentApplyStatusOut(**agent_apply_state.snapshot())

    try:
        criteria = load_criteria()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not criteria.applicant_profile.is_complete():
        raise HTTPException(
            status_code=400,
            detail="applicant_profile is incomplete (full_name/email/phone required) — fill it in in Settings.",
        )

    agent_apply_state.reset_for_new_run()
    background_tasks.add_task(_run_in_background)
    return AgentApplyStatusOut(**agent_apply_state.snapshot())


@router.post("/setup-profile")
def setup_profile() -> dict:
    """Open worker 0's Chrome profile visibly, outside of any run, so you
    can sign into a small set of sites by hand. Whatever you log into here
    persists in that profile for future agent-apply runs — close the window
    yourself when you're done."""
    try:
        chrome.launch_login_chrome(worker_id=0)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "opened": True,
        "suggested_logins": [
            "Your email account you use for job applications (needed if any site sends a verification email you must click through by hand).",
            "LinkedIn (some ATS forms offer 'autofill from LinkedIn' — optional, purely a convenience).",
            "Google (optional — if signed in here, the agent may click an already-authenticated account tile on a 'Sign in with Google' wall instead of stopping; it never types a Google email or password itself).",
        ],
        "note": "This pipeline never types a password into Google/Microsoft/Okta/etc. or attempts fresh OAuth sign-in itself. It also won't create accounts on job sites unless you've filled in a reusable signup email/password under Settings — logging in here only helps on forms that check for an existing session (or, for Google specifically, an existing account tile).",
    }


def _run_in_background() -> None:
    def on_result(application: Application) -> None:
        if application.status == ApplicationStatus.SUBMITTED:
            agent_apply_state.submitted_count += 1
        elif application.status == ApplicationStatus.FAILED:
            agent_apply_state.failed_count += 1
        else:
            agent_apply_state.unsupported_count += 1

    try:
        criteria = load_criteria()
        run_agent_apply(
            criteria,
            log=agent_apply_state.log,
            should_stop=agent_apply_state.should_stop,
            on_result=on_result,
            on_cost=agent_apply_state.add_cost,
            on_usage=agent_apply_usage.record_job,
        )
        if agent_apply_state.should_stop():
            agent_apply_state.finish(stopped=True)
        else:
            agent_apply_state.finish()
    except Exception as exc:
        logger.exception("Agent-apply run crashed")
        agent_apply_state.log(f"ERROR: {exc}")
        agent_apply_state.finish(error=str(exc))
