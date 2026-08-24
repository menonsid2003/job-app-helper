from pathlib import Path

from app.apply_agent import chrome


def test_reset_worker_dir_returns_an_absolute_path_even_when_configured_relatively(tmp_path: Path, monkeypatch):
    """Must be absolute — orchestrator.py's run_job also sets this directory
    as the claude subprocess's cwd, so a relative --mcp-config path built
    from it would get resolved against itself and double up (the exact
    "MCP config file not found: .../worker-0/data/agent_apply_work/worker-0/
    mcp-config.json" bug this guards against). chdir's into tmp_path so a
    relative setting (the default, "data/agent_apply_work") doesn't create a
    stray directory in the real repo."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.config.settings.agent_apply_work_dir", Path("relative_work_dir"))

    worker_dir = chrome.reset_worker_dir(0)

    assert worker_dir.is_absolute()
    assert worker_dir.exists()


def test_reset_worker_dir_wipes_existing_contents(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.agent_apply_work_dir", tmp_path / "work")

    first = chrome.reset_worker_dir(0)
    (first / "stale.txt").write_text("leftover from a previous run", encoding="utf-8")
    second = chrome.reset_worker_dir(0)

    assert first == second
    assert not (second / "stale.txt").exists()
