"""Chrome lifecycle management for agent-apply workers.

Launches an isolated, blank Chrome profile per worker with remote debugging
enabled, so the `claude` CLI can drive it via Playwright MCP over CDP.
Profiles are never copied from an existing Chrome profile — each worker
starts empty and only accumulates whatever cookies/logins accrue from
sessions you sign into by hand (see setup_login_chrome) or that the agent
itself creates while applying. This keeps agent-apply from ever touching or
inheriting session data from your everyday browsing profile.
"""

import json
import logging
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# CDP port base — each worker uses BASE_CDP_PORT + worker_id
BASE_CDP_PORT = 9333

_chrome_procs: dict[int, subprocess.Popen] = {}
_chrome_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Cross-platform process helpers
# ---------------------------------------------------------------------------

def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its children (Chrome spawns 10+ helper
    processes on Windows, so taskkill /T is needed to get the whole tree)."""
    import signal as _signal

    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        else:
            import os
            try:
                os.killpg(os.getpgid(pid), _signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    os.kill(pid, _signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    except Exception:
        logger.debug("Failed to kill process tree for PID %d", pid, exc_info=True)


def _kill_on_port(port: int) -> None:
    """Kill any process listening on a specific port (zombie cleanup)."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        _kill_process_tree(int(pid))
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=10,
            )
            for pid_str in result.stdout.strip().splitlines():
                pid_str = pid_str.strip()
                if pid_str.isdigit():
                    _kill_process_tree(int(pid_str))
    except FileNotFoundError:
        logger.debug("Port-kill tool not found (netstat/lsof) for port %d", port)
    except Exception:
        logger.debug("Failed to kill process on port %d", port, exc_info=True)


# ---------------------------------------------------------------------------
# Chrome executable discovery
# ---------------------------------------------------------------------------

_WINDOWS_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_MACOS_CANDIDATES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
_LINUX_CANDIDATES = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium-browser"]


def get_chrome_executable() -> str:
    if settings.agent_apply_chrome_executable:
        return settings.agent_apply_chrome_executable

    system = platform.system()
    candidates = {"Windows": _WINDOWS_CANDIDATES, "Darwin": _MACOS_CANDIDATES}.get(system, _LINUX_CANDIDATES)
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    raise RuntimeError(
        "Could not find a Chrome install. Set AGENT_APPLY_CHROME_EXECUTABLE in .env to its full path."
    )


# ---------------------------------------------------------------------------
# Worker profile management
# ---------------------------------------------------------------------------

def worker_profile_dir(worker_id: int) -> Path:
    return settings.agent_apply_worker_dir / f"worker-{worker_id}"


def _suppress_restore_nag(profile_dir: Path) -> None:
    """Clear Chrome's 'restore pages' nag and disable the password
    manager/autofill save prompts, which would otherwise pop up and block
    the agent mid-run. No-op until the profile has been launched at least
    once (Preferences doesn't exist before that)."""
    prefs_file = profile_dir / "Default" / "Preferences"
    if not prefs_file.exists():
        return
    try:
        prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
        prefs.setdefault("profile", {})["exit_type"] = "Normal"
        prefs.setdefault("session", {})["restore_on_startup"] = 4  # 4 = open blank
        prefs.setdefault("session", {}).pop("startup_urls", None)
        prefs["credentials_enable_service"] = False
        prefs.setdefault("password_manager", {})["saving_enabled"] = False
        prefs.setdefault("autofill", {})["profile_enabled"] = False
        prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        logger.debug("Could not patch Chrome preferences", exc_info=True)


# ---------------------------------------------------------------------------
# Chrome launch / kill
# ---------------------------------------------------------------------------

def _base_chrome_args(profile_dir: Path) -> list[str]:
    return [
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1024,768",
        "--disable-session-crashed-bubble",
        "--disable-features=InfiniteSessionRestore,PasswordManagerOnboarding",
        "--hide-crash-restore-bubble",
        "--noerrdialogs",
        "--password-store=basic",
        "--disable-save-password-bubble",
        "--disable-popup-blocking",
        # Block dangerous permissions at browser level
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--deny-permission-prompts",
        "--disable-notifications",
    ]


def _spawn(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if platform.system() != "Windows":
        import os
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def launch_chrome(worker_id: int, port: int | None = None, headless: bool = False) -> subprocess.Popen:
    """Launch a worker's Chrome with remote debugging for the agent to drive.

    The profile directory is created blank on first use and never touched
    again except by Chrome itself (and _suppress_restore_nag) — no copying
    from any other profile.
    """
    if port is None:
        port = BASE_CDP_PORT + worker_id

    profile_dir = worker_profile_dir(worker_id)
    profile_dir.mkdir(parents=True, exist_ok=True)

    _kill_on_port(port)
    _suppress_restore_nag(profile_dir)

    cmd = [get_chrome_executable(), f"--remote-debugging-port={port}", *_base_chrome_args(profile_dir)]
    if headless:
        cmd.append("--headless=new")

    proc = _spawn(cmd)
    with _chrome_lock:
        _chrome_procs[worker_id] = proc

    time.sleep(3)  # give Chrome time to open the debug port
    logger.info("[worker-%d] Chrome started on port %d (pid %d)", worker_id, port, proc.pid)
    return proc


def launch_login_chrome(worker_id: int = 0) -> subprocess.Popen:
    """Open a worker's profile visibly with NO remote debugging and no
    agent attached, so you can sign into sites by hand. Whatever you log
    into here persists in that profile for future agent-apply runs. Close
    the window yourself when you're done."""
    profile_dir = worker_profile_dir(worker_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _suppress_restore_nag(profile_dir)

    cmd = [get_chrome_executable(), *_base_chrome_args(profile_dir)]
    proc = _spawn(cmd)
    with _chrome_lock:
        _chrome_procs[worker_id] = proc
    logger.info("[worker-%d] Login Chrome started (pid %d)", worker_id, proc.pid)
    return proc


def cleanup_worker(worker_id: int, process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        _kill_process_tree(process.pid)
    with _chrome_lock:
        _chrome_procs.pop(worker_id, None)
    logger.info("[worker-%d] Chrome cleaned up", worker_id)


def kill_all_chrome() -> None:
    with _chrome_lock:
        procs = dict(_chrome_procs)
        _chrome_procs.clear()

    for wid, proc in procs.items():
        if proc.poll() is None:
            _kill_process_tree(proc.pid)
        _kill_on_port(BASE_CDP_PORT + wid)

    _kill_on_port(BASE_CDP_PORT)


def reset_worker_dir(worker_id: int) -> Path:
    """Wipe and recreate a worker's Claude-subprocess working directory (kept
    separate from the Chrome profile dir, which must persist across runs).

    Returns an ABSOLUTE path — this becomes both the subprocess's cwd and the
    base for its --mcp-config argument (see orchestrator.py's run_job). If it
    were left relative, claude would resolve that relative --mcp-config path
    against its own cwd (this same directory), doubling the path — exactly
    the "MCP config file not found: .../worker-0/data/agent_apply_work/
    worker-0/mcp-config.json" error this fixes.
    """
    worker_dir = (settings.agent_apply_work_dir / f"worker-{worker_id}").resolve()
    if worker_dir.exists():
        shutil.rmtree(str(worker_dir), ignore_errors=True)
    worker_dir.mkdir(parents=True, exist_ok=True)
    return worker_dir


def cleanup_on_exit() -> None:
    """Register with atexit at application startup to sweep any orphan
    Chrome processes/ports on shutdown."""
    kill_all_chrome()
