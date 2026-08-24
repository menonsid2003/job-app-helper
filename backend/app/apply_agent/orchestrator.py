"""Agent-apply orchestration: claim eligible jobs, launch Chrome + the
`claude` CLI for each one, parse the result, and record an Application row.

Mirrors app.auto_apply's shape (acquire -> apply -> record) but drives a
real Chrome via the AI agent instead of a hand-coded Playwright adapter, and
supports multiple parallel Chrome/worker instances.
"""

import atexit
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.apply_agent import chrome
from app.apply_agent.prompt import build_prompt
from app.config import settings
from app.db import SessionLocal
from app.google_sheets import push_applied_job
from app.llm.base import LLMProvider
from app.llm.factory import make_default_provider
from app.models import Application, ApplicationMethod, ApplicationStatus, Job, JobStatus, Resume, Score
from app.resume import load_base_resume_text
from app.resume_service import create_tailored_resume
from app.schemas import ApplicantProfile, CriteriaConfig, ScoreOut

logger = logging.getLogger(__name__)

atexit.register(chrome.cleanup_on_exit)

# Deliberately narrower than auto_apply.py's ELIGIBLE_STATUSES (which also
# includes PURSUE) — agent-apply only ever picks up a job once you've
# explicitly tailored it (manually, via Tailor All, or auto-tailored inline
# by auto_apply.py), never a bare "Track". A job sitting at PURSUE is
# ignored here until you change that yourself.
ELIGIBLE_STATUSES = (JobStatus.TAILORED,)

# Failure reasons that should never be retried by a future run — mirrors the
# job-level statuses this pipeline can return, kept separate from
# auto_apply's adapter-level ApplicationStatus.UNSUPPORTED distinction: here
# every non-submitted result is recorded as UNSUPPORTED (permanent) or
# FAILED (worth retrying), same as the Tracking table already expects.
PERMANENT_FAILURES: set[str] = {
    "expired", "captcha", "login_issue", "not_eligible_location",
    "not_a_job_application", "unsafe_permissions", "unsafe_verification",
    "sso_required", "manual_email_required", "unanswerable_question",
}


def _is_permanent_failure(result: str) -> bool:
    reason = result.split(":", 1)[-1] if ":" in result else result
    return result in PERMANENT_FAILURES or reason in PERMANENT_FAILURES


def _already_applied(db: Session, job_id: int) -> bool:
    existing = db.execute(
        select(Application).where(Application.job_id == job_id, Application.status == ApplicationStatus.SUBMITTED)
    ).scalar_one_or_none()
    return existing is not None


def _latest_resume(db: Session, job_id: int) -> Resume | None:
    return db.execute(
        select(Resume).where(Resume.job_id == job_id).order_by(Resume.version.desc()).limit(1)
    ).scalar_one_or_none()


def _latest_score(db: Session, job_id: int) -> Score | None:
    return db.execute(
        select(Score).where(Score.job_id == job_id).order_by(Score.scored_at.desc()).limit(1)
    ).scalar_one_or_none()


def _record(
    db: Session, job: Job, resume: Resume | None, status: ApplicationStatus, notes: str, criteria: CriteriaConfig
) -> Application:
    application = Application(
        job_id=job.id,
        resume_id=resume.id if resume else None,
        status=status,
        method=ApplicationMethod.AGENT,
        notes=notes,
        submitted_at=datetime.now(timezone.utc) if status == ApplicationStatus.SUBMITTED else None,
    )
    db.add(application)
    if status == ApplicationStatus.SUBMITTED:
        job.status = JobStatus.APPLIED
    db.commit()
    db.refresh(application)
    if status == ApplicationStatus.SUBMITTED:
        push_applied_job(job, criteria, method_label="Agent")
    return application


class _ClaimTable:
    """In-memory job claim tracker for one agent-apply run, guarding against
    two parallel workers picking up the same job. Not persisted to the DB
    (unlike the reference pipeline's apply_status='in_progress' column) —
    this app's Job model has no such column, and a run lives entirely within
    one process, so an in-memory set is enough."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: set[int] = set()

    def acquire(self, db: Session) -> Job | None:
        with self._lock:
            stmt = select(Job).where(Job.status.in_(ELIGIBLE_STATUSES)).order_by(Job.id)
            if self._claimed:
                stmt = stmt.where(Job.id.notin_(self._claimed))
            for job in db.execute(stmt).scalars().all():
                if _already_applied(db, job.id):
                    continue
                self._claimed.add(job.id)
                return job
            return None

    def release(self, job_id: int) -> None:
        with self._lock:
            self._claimed.discard(job_id)


def _make_mcp_config(cdp_port: int) -> dict:
    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": [
                    "@playwright/mcp@latest",
                    f"--cdp-endpoint=http://localhost:{cdp_port}",
                    "--viewport-size=1024,768",
                ],
            },
        }
    }


def run_job(
    job: Job, resume: Resume, score: ScoreOut | None, profile: ApplicantProfile,
    criteria: CriteriaConfig, port: int, worker_id: int, dry_run: bool,
    log: Callable[[str], None],
) -> tuple[str, int, float, dict]:
    """Spawn a `claude` CLI subprocess for one job application.

    Returns (status, duration_ms, cost_usd, usage). status is one of:
    'applied', 'expired', 'captcha', 'login_issue', or 'failed:reason'.
    cost_usd is claude's own token-usage-priced estimate (tokens x standard
    API list price) — it's populated the same way under a Pro/Max
    subscription as under a metered API key, since it reflects usage, not
    what was actually billed. Under a subscription you're not charged
    per-token at all (flat fee, usage counted against rate-limit windows
    instead), so treat this as a reference/equivalent-value figure, not
    money actually spent.

    usage is a dict with the actual token counts from the CLI's "result"
    message (input_tokens/output_tokens/cache_read_input_tokens/
    cache_creation_input_tokens) plus, if a "rate_limit_event" message was
    seen, the account's real 5-hour-window status (rate_limit_status/
    rate_limit_resets_at/rate_limit_type) — this is the actual subscription
    session-usage signal, unlike the dollar estimate above. Empty dict if
    the run failed before any such message arrived.
    """
    worker_dir = chrome.reset_worker_dir(worker_id)

    try:
        agent_prompt = build_prompt(
            job=job, resume=resume, score=score, profile=profile, criteria=criteria,
            worker_dir=worker_dir, capsolver_api_key=settings.capsolver_api_key, dry_run=dry_run,
        )
    except ValueError as exc:
        return f"failed:{exc}", 0, 0.0, {}

    mcp_config_path = worker_dir / "mcp-config.json"
    mcp_config_path.write_text(json.dumps(_make_mcp_config(port)), encoding="utf-8")

    cmd = [
        settings.claude_cli_path,
        "--model", settings.claude_model,
        "-p",
        "--mcp-config", str(mcp_config_path),
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--output-format", "stream-json",
        "--verbose", "-",
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    settings.agent_apply_log_dir.mkdir(parents=True, exist_ok=True)
    worker_log = settings.agent_apply_log_dir / f"worker-{worker_id}.log"
    ts_header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_header = (
        f"\n{'=' * 60}\n[{ts_header}] {job.title} @ {job.company}\n"
        f"URL: {job.canonical_url or job.source_url}\n{'=' * 60}\n"
    )

    # Real files, not subprocess.PIPE, for stdin/stdout. On Windows, PIPE
    # requires duplicating the parent process's own console handles — if
    # this backend was started without a real console attached (a detached
    # process, a service, etc.), that fails with the misleadingly generic
    # OSError: [Errno 22] Invalid argument, even though Chrome (launched
    # with stdout/stderr=DEVNULL, no PIPE at all) starts up just fine.
    # Redirecting to files sidesteps that class of bug entirely.
    prompt_path = worker_dir / "prompt.txt"
    prompt_path.write_text(agent_prompt, encoding="utf-8")
    output_path = worker_dir / "output.jsonl"

    start = time.time()
    proc = None
    try:
        with open(prompt_path, encoding="utf-8") as stdin_f, open(output_path, "w", encoding="utf-8") as stdout_f:
            proc = subprocess.Popen(
                cmd, stdin=stdin_f, stdout=stdout_f, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, cwd=str(worker_dir),
            )

            text_parts: list[str] = []
            cost_usd = 0.0
            usage: dict = {}
            with open(worker_log, "a", encoding="utf-8") as lf, \
                 open(output_path, encoding="utf-8", errors="replace") as tail_f:
                lf.write(log_header)
                # Tail output.jsonl as claude writes to it, same live per-tool
                # logging as before — just reading a growing file instead of
                # a pipe, since the process is writing to stdout_f directly.
                while True:
                    line = tail_f.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.3)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        msg_type = msg.get("type")
                        if msg_type == "assistant":
                            for block in msg.get("message", {}).get("content", []):
                                bt = block.get("type")
                                if bt == "text":
                                    text_parts.append(block["text"])
                                    lf.write(block["text"] + "\n")
                                elif bt == "tool_use":
                                    name = block.get("name", "").replace("mcp__playwright__", "")
                                    lf.write(f"  >> {name}\n")
                                    log(f"[W{worker_id}] {name}")
                        elif msg_type == "result":
                            text_parts.append(msg.get("result", ""))
                            # Token-usage-priced estimate, not an actual charge
                            # under subscription auth — see run_job's docstring.
                            cost_usd = msg.get("total_cost_usd", 0.0) or 0.0
                            result_usage = msg.get("usage") or {}
                            for key in (
                                "input_tokens", "output_tokens",
                                "cache_read_input_tokens", "cache_creation_input_tokens",
                            ):
                                if key in result_usage:
                                    usage[key] = result_usage[key]
                        elif msg_type == "rate_limit_event":
                            # The account's actual 5-hour-window status —
                            # keep the latest one seen this run.
                            info = msg.get("rate_limit_info") or {}
                            if info:
                                usage["rate_limit_status"] = info.get("status")
                                usage["rate_limit_resets_at"] = info.get("resetsAt")
                                usage["rate_limit_type"] = info.get("rateLimitType")
                    except json.JSONDecodeError:
                        text_parts.append(line)
                        lf.write(line + "\n")

        proc.wait(timeout=300)
        proc = None

        output = "\n".join(text_parts)
        duration_ms = int((time.time() - start) * 1000)

        for result_status in ["APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"]:
            if f"RESULT:{result_status}" in output:
                return result_status.lower(), duration_ms, cost_usd, usage

        if "RESULT:FAILED" in output:
            for out_line in output.split("\n"):
                if "RESULT:FAILED" in out_line:
                    tail = out_line[out_line.index("FAILED") + 6:]
                    reason = out_line.split("RESULT:FAILED:")[-1].strip() if ":" in tail else "unknown"
                    reason = re.sub(r'[*`"]+$', '', reason).strip()
                    return f"failed:{reason}", duration_ms, cost_usd, usage
            return "failed:unknown", duration_ms, cost_usd, usage

        return "failed:no_result_line", duration_ms, cost_usd, usage

    except subprocess.TimeoutExpired:
        return "failed:timeout", int((time.time() - start) * 1000), 0.0, {}
    except FileNotFoundError:
        return f"failed:claude CLI not found (checked '{settings.claude_cli_path}')", 0, 0.0, {}
    except Exception as exc:
        logger.exception("Agent-apply job crashed")
        return f"failed:{str(exc)[:150]}", int((time.time() - start) * 1000), 0.0, {}
    finally:
        if proc is not None and proc.poll() is None:
            chrome._kill_process_tree(proc.pid)


def worker_loop(
    worker_id: int, criteria: CriteriaConfig, claims: _ClaimTable, dry_run: bool,
    log: Callable[[str], None], should_stop: Callable[[], bool],
    on_result: Callable[[Application], None],
    tailor_provider: LLMProvider | None, base_resume_text: str | None,
    on_cost: Callable[[float], None] = lambda cost: None,
    on_usage: Callable[[dict], None] = lambda usage: None,
) -> None:
    db = SessionLocal()
    port = chrome.BASE_CDP_PORT + worker_id
    try:
        while not should_stop():
            job = claims.acquire(db)
            if job is None:
                break

            resume = _latest_resume(db, job.id)
            if resume is None:
                if tailor_provider is None:
                    log(f"[W{worker_id}] Skipping {job.title} @ {job.company}: no tailored resume yet (and no base resume configured to auto-tailor one).")
                    on_result(_record(db, job, None, ApplicationStatus.UNSUPPORTED, "No tailored resume — tailor one first.", criteria))
                    claims.release(job.id)
                    continue
                log(f"[W{worker_id}] Tailoring resume for {job.title} @ {job.company}…")
                try:
                    resume = create_tailored_resume(db, job, tailor_provider, base_resume_text)
                except Exception as exc:
                    logger.exception("Auto-tailoring failed for job %s", job.id)
                    log(f"[W{worker_id}] Could not tailor resume for {job.title} @ {job.company}: {exc}")
                    on_result(_record(db, job, None, ApplicationStatus.UNSUPPORTED, f"Resume tailoring failed: {exc}", criteria))
                    claims.release(job.id)
                    continue

            score_row = _latest_score(db, job.id)
            score = ScoreOut.model_validate(score_row) if score_row else None

            chrome_proc = None
            try:
                log(f"[W{worker_id}] Launching Chrome: {job.title} @ {job.company}...")
                chrome_proc = chrome.launch_chrome(worker_id, port=port, headless=settings.agent_apply_headless)

                result, _duration_ms, cost_usd, usage = run_job(
                    job=job, resume=resume, score=score, profile=criteria.applicant_profile,
                    criteria=criteria, port=port, worker_id=worker_id, dry_run=dry_run, log=log,
                )
                if cost_usd:
                    on_cost(cost_usd)
                    log(f"[W{worker_id}] Est. cost: ${cost_usd:.3f}")
                if usage:
                    on_usage({**usage, "cost_usd": cost_usd})

                if result == "applied":
                    log(f"[W{worker_id}] APPLIED: {job.title} @ {job.company}")
                    on_result(_record(db, job, resume, ApplicationStatus.SUBMITTED, "Applied via AI agent.", criteria))
                else:
                    reason = result.split(":", 1)[-1] if ":" in result else result
                    status = ApplicationStatus.UNSUPPORTED if _is_permanent_failure(result) else ApplicationStatus.FAILED
                    log(f"[W{worker_id}] {status.value.upper()}: {job.title} @ {job.company} — {reason}")
                    on_result(_record(db, job, resume, status, reason, criteria))
            except Exception as exc:
                logger.exception("Agent-apply worker %d crashed on job %d", worker_id, job.id)
                log(f"[W{worker_id}] ERROR: {exc}")
                on_result(_record(db, job, resume, ApplicationStatus.FAILED, f"Unexpected error: {exc}", criteria))
            finally:
                claims.release(job.id)
                if chrome_proc:
                    chrome.cleanup_worker(worker_id, chrome_proc)
    finally:
        db.close()


def run_agent_apply(
    criteria: CriteriaConfig,
    log: Callable[[str], None] = lambda msg: None,
    should_stop: Callable[[], bool] = lambda: False,
    on_result: Callable[[Application], None] = lambda application: None,
    on_cost: Callable[[float], None] = lambda cost: None,
    on_usage: Callable[[dict], None] = lambda usage: None,
    workers: int | None = None,
    dry_run: bool = False,
) -> None:
    """Entry point for a background-triggered agent-apply run (see
    app.routers.agent_apply). Each worker gets its own Chrome instance, CDP
    port, and DB session; they claim jobs from a shared in-memory queue."""
    if not criteria.applicant_profile.is_complete():
        raise RuntimeError("applicant_profile is incomplete (full_name/email/phone required) — fill it in in Settings.")

    worker_count = workers or settings.agent_apply_workers
    claims = _ClaimTable()

    # Loaded once up front so a pursued job with no resume yet gets tailored
    # on the fly instead of being skipped as unsupported. Missing base resume
    # isn't fatal to the whole run — only jobs that actually need tailoring
    # are affected.
    try:
        base_resume_text: str | None = load_base_resume_text()
        tailor_provider: LLMProvider | None = make_default_provider()
    except FileNotFoundError:
        base_resume_text = None
        tailor_provider = None

    log(f"Agent-apply: starting {worker_count} worker(s).")

    if worker_count == 1:
        worker_loop(0, criteria, claims, dry_run, log, should_stop, on_result, tailor_provider, base_resume_text, on_cost, on_usage)
        return

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent-apply-worker") as executor:
        futures = [
            executor.submit(
                worker_loop, i, criteria, claims, dry_run, log, should_stop, on_result,
                tailor_provider, base_resume_text, on_cost, on_usage,
            )
            for i in range(worker_count)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("Agent-apply worker crashed")
