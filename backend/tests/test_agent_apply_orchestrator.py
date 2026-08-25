"""run_job's subprocess I/O uses real files for stdin/stdout instead of
subprocess.PIPE (see app/apply_agent/orchestrator.py) specifically to avoid
a Windows bug where PIPE-based redirection fails with a misleading
"OSError: [Errno 22] Invalid argument" when the parent process lacks a real
console. These tests fake subprocess.Popen itself (writing canned
stream-json output into the real file handle run_job hands it) so they
exercise the actual file-tailing/parsing contract without needing a working
`claude` CLI installed."""

import json
from pathlib import Path

import pytest

from app.apply_agent.orchestrator import _ClaimTable, run_job
from app.models import Application, ApplicationMethod, ApplicationStatus, Job, JobStatus, Resume
from app.schemas import ApplicantProfile, CriteriaConfig


class FakeProc:
    """Stand-in for subprocess.Popen's return value. run_job's tailing loop
    checks .poll() independently of .wait() (same as a real Popen — a real
    child process shows as exited via .poll() the moment it exits, whether
    or not anyone has called .wait() yet), so this must report "done"
    immediately, not lazily wait for .wait() to be called."""

    def __init__(self):
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _fake_popen_factory(stream_json_lines: list[dict], captured_cmds: list[list[str]] | None = None):
    """Returns a fake subprocess.Popen that writes the given messages to the
    stdout file handle it's given (simulating what a real `claude -p
    --output-format stream-json` process would write) and reports done.
    captured_cmds, if given, records each cmd list actually passed."""

    def fake_popen(cmd, stdin, stdout, stderr, text, encoding, errors, env, cwd):
        if captured_cmds is not None:
            captured_cmds.append(cmd)
        for msg in stream_json_lines:
            stdout.write(json.dumps(msg) + "\n")
        stdout.flush()
        return FakeProc()

    return fake_popen


@pytest.fixture
def job() -> Job:
    return Job(
        id=1, source="jobspy", source_url="https://example.com/job/1", canonical_url=None,
        title="Data Engineer", company="Acme", location="Remote",
        description="Build pipelines.", status=JobStatus.PURSUE,
    )


@pytest.fixture
def resume(tmp_path: Path) -> Resume:
    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake\n")
    txt_path = tmp_path / "resume.txt"
    txt_path.write_text("Jane Doe\nData Engineer with 5 years experience.", encoding="utf-8")
    return Resume(id=1, job_id=1, version=1, pdf_path=str(pdf_path), ats_text_path=str(txt_path), diff_summary="")


@pytest.fixture
def criteria() -> CriteriaConfig:
    return CriteriaConfig(
        applicant_profile=ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")
    )


@pytest.fixture(autouse=True)
def _isolate_worker_dirs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.agent_apply_work_dir", tmp_path / "work")
    monkeypatch.setattr("app.config.settings.agent_apply_log_dir", tmp_path / "logs")


def _run(monkeypatch, stream_json_lines, job, resume, criteria):
    monkeypatch.setattr(
        "app.apply_agent.orchestrator.subprocess.Popen", _fake_popen_factory(stream_json_lines)
    )
    return run_job(
        job=job, resume=resume, score=None, profile=criteria.applicant_profile, criteria=criteria,
        port=9333, worker_id=0, dry_run=False, log=lambda msg: None,
    )


def test_run_job_parses_applied_result_via_file_redirected_io(monkeypatch, job, resume, criteria):
    stream = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Applying now."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__playwright__browser_navigate", "input": {}}]}},
        {"type": "result", "result": "RESULT:APPLIED", "total_cost_usd": 0.0421},
    ]

    status, duration_ms, cost_usd, usage = _run(monkeypatch, stream, job, resume, criteria)

    assert status == "applied"
    assert duration_ms >= 0
    assert cost_usd == pytest.approx(0.0421)
    assert usage == {}


def test_run_job_passes_an_absolute_mcp_config_path_that_actually_exists(monkeypatch, job, resume, criteria):
    """Regression test: --mcp-config used to be built from a relative
    worker_dir, and since the subprocess's cwd is ALSO that worker_dir,
    claude resolved the relative --mcp-config against its own cwd and
    doubled the path — "MCP config file not found: .../worker-0/data/
    agent_apply_work/worker-0/mcp-config.json". reset_worker_dir now always
    returns an absolute path, so this can't happen regardless of the
    subprocess's cwd."""
    captured_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "app.apply_agent.orchestrator.subprocess.Popen",
        _fake_popen_factory([{"type": "result", "result": "RESULT:APPLIED"}], captured_cmds),
    )

    run_job(
        job=job, resume=resume, score=None, profile=criteria.applicant_profile, criteria=criteria,
        port=9333, worker_id=0, dry_run=False, log=lambda msg: None,
    )

    assert len(captured_cmds) == 1
    mcp_config_arg = captured_cmds[0][captured_cmds[0].index("--mcp-config") + 1]
    assert Path(mcp_config_arg).is_absolute()
    assert Path(mcp_config_arg).exists()


def test_run_job_parses_failed_result_with_reason(monkeypatch, job, resume, criteria):
    stream = [{"type": "result", "result": "RESULT:FAILED:stuck", "total_cost_usd": 0.01}]

    status, _duration_ms, cost_usd, _usage = _run(monkeypatch, stream, job, resume, criteria)

    assert status == "failed:stuck"
    assert cost_usd == pytest.approx(0.01)


def test_run_job_returns_zero_cost_under_subscription_auth(monkeypatch, job, resume, criteria):
    """Claude subscription (not a metered API key) auth reports no cost."""
    stream = [{"type": "result", "result": "RESULT:APPLIED"}]  # no total_cost_usd field at all

    status, _duration_ms, cost_usd, _usage = _run(monkeypatch, stream, job, resume, criteria)

    assert status == "applied"
    assert cost_usd == 0.0


def test_run_job_extracts_token_usage_and_rate_limit_status(monkeypatch, job, resume, criteria):
    stream = [
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "resetsAt": 1787459400, "rateLimitType": "five_hour"},
        },
        {
            "type": "result",
            "result": "RESULT:APPLIED",
            "total_cost_usd": 0.32,
            "usage": {
                "input_tokens": 28,
                "output_tokens": 2823,
                "cache_read_input_tokens": 750899,
                "cache_creation_input_tokens": 34261,
            },
        },
    ]

    status, _duration_ms, _cost_usd, usage = _run(monkeypatch, stream, job, resume, criteria)

    assert status == "applied"
    assert usage["input_tokens"] == 28
    assert usage["output_tokens"] == 2823
    assert usage["cache_read_input_tokens"] == 750899
    assert usage["cache_creation_input_tokens"] == 34261
    assert usage["rate_limit_status"] == "allowed"
    assert usage["rate_limit_resets_at"] == 1787459400
    assert usage["rate_limit_type"] == "five_hour"


def test_run_job_writes_transcript_to_worker_log(monkeypatch, job, resume, criteria, tmp_path):
    stream = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking the form."}]}},
        {"type": "result", "result": "RESULT:APPLIED", "total_cost_usd": 0.01},
    ]

    _run(monkeypatch, stream, job, resume, criteria)

    worker_log = tmp_path / "logs" / "worker-0.log"
    assert worker_log.exists()
    log_text = worker_log.read_text(encoding="utf-8")
    assert "Data Engineer" in log_text  # job title in the run header
    assert "Checking the form." in log_text


def _make_tailored_job(db_session) -> Job:
    job = Job(
        source="greenhouse", source_url="https://example.com/1", canonical_url=None,
        title="Data Engineer", company="Acme", location="Remote", description="x",
        status=JobStatus.TAILORED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_claim_table_never_reclaims_a_job_already_attempted_this_run(db_session):
    """Regression test for the auto-apply crash-loop bug: release() only
    frees the concurrency guard for other workers, it must not make the job
    claimable again by the *next* acquire() in the same run — otherwise a
    job that deterministically fails (bad CAPTCHA, unanswerable question)
    gets relaunched through a full Chrome + claude-CLI session forever,
    since a failure alone doesn't change job.status away from TAILORED."""
    job = _make_tailored_job(db_session)
    claims = _ClaimTable()

    claimed = claims.acquire(db_session)
    assert claimed is not None and claimed.id == job.id

    claims.release(job.id)  # worker finished (successfully or not) and freed its slot

    assert claims.acquire(db_session) is None


def test_claim_table_skips_job_already_marked_permanently_unsupported(db_session):
    """A job whose most recent attempt came back UNSUPPORTED (a permanent
    failure per PERMANENT_FAILURES) must not be picked up by a *later* run
    either — otherwise every future "Run Agent-Apply" click re-spends a full
    Chrome + claude-CLI session on something already known to fail."""
    job = _make_tailored_job(db_session)
    db_session.add(Application(
        job_id=job.id, status=ApplicationStatus.UNSUPPORTED, method=ApplicationMethod.AGENT, notes="captcha",
    ))
    db_session.commit()

    assert _ClaimTable().acquire(db_session) is None


def test_claim_table_still_offers_a_job_whose_last_attempt_only_failed(db_session):
    """FAILED (as opposed to UNSUPPORTED) means the prior attempt hit
    something transient — a timeout, a crash — so unlike the permanent case
    above, a fresh run should still be allowed to try it again."""
    job = _make_tailored_job(db_session)
    db_session.add(Application(
        job_id=job.id, status=ApplicationStatus.FAILED, method=ApplicationMethod.AGENT, notes="timeout",
    ))
    db_session.commit()

    claimed = _ClaimTable().acquire(db_session)
    assert claimed is not None and claimed.id == job.id
