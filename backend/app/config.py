from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # "ollama" or "anthropic" — swap providers via .env, no code change needed.
    # LLMProvider is an interface (app/llm/base.py); make_default_provider()
    # (app/llm/factory.py) picks the implementation based on this setting.
    llm_provider: str = "ollama"

    ollama_host: str = "http://192.168.50.6:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout_seconds: float = 120.0
    ollama_max_retries: int = 3

    # Never hardcoded — supply your own key via .env, or leave unset and rely
    # on the anthropic SDK's own credential resolution (ANTHROPIC_API_KEY env
    # var already covers that; only set this if you want it pinned here too).
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_max_tokens: int = 4096
    anthropic_max_retries: int = 2

    database_url: str = "sqlite:///./data/job_app_helper.db"

    criteria_config_path: Path = Path("criteria.yaml")
    base_resume_ats_path: Path = Path("resume/base_resume.txt")
    # Optional, separate from the base resume: everything else you've done
    # (older roles, one-off projects, retired skills) that doesn't fit the
    # base resume's one-page layout but is still fair game for tailoring to
    # pull in when a specific job calls for it. Empty by default — tailoring
    # falls back to the base resume alone when this isn't filled in.
    experience_bank_path: Path = Path("resume/experience_bank.txt")
    tailored_resume_dir: Path = Path("data/resumes")
    application_screenshot_dir: Path = Path("data/application_screenshots")

    # Never hardcoded — supply your own key via .env. Auto-apply refuses to
    # submit through a CAPTCHA-gated form without this set.
    capsolver_api_key: str | None = None

    # Google Sheets push (app/google_sheets.py) is opt-in via
    # criteria.google_sheets.enabled, but the credential itself is a
    # service-account JSON key file — same "secret, so it lives outside
    # criteria.yaml/the web UI" rule as the API keys above. Drop the file
    # Google Cloud gives you at this path (default: backend/google/
    # service_account.json) and share the target sheet with its
    # client_email as Editor.
    google_sheets_credentials_path: Path = Path("google/service_account.json")

    cors_origins: list[str] = ["http://localhost:5173"]

    # ---- AI agent apply (separate, opt-in path from the adapter-based
    # auto-apply above — drives a real visible Chrome via the `claude` CLI +
    # Playwright MCP instead of hand-coded per-ATS selectors) ----

    # Each worker's Chrome profile starts completely blank — never copied or
    # cloned from your everyday browsing profile, so it can't inherit
    # unrelated session cookies. Use the one-time "open Chrome to log in"
    # action (Settings page) to sign into a small, deliberate set of sites in
    # that blank profile; it persists across runs from then on.

    # Leave unset to auto-detect the system Chrome install.
    agent_apply_chrome_executable: str | None = None

    agent_apply_worker_dir: Path = Path("data/agent_apply_chrome_workers")
    agent_apply_work_dir: Path = Path("data/agent_apply_work")
    agent_apply_log_dir: Path = Path("data/agent_apply_logs")
    # Cumulative token usage + the account's last-seen 5-hour rate-limit
    # window status — persisted separately from AgentApplyRunState (which
    # zeroes its counters at the start of every run) so it survives restarts
    # and stays visible even when no run is currently active.
    agent_apply_usage_path: Path = Path("data/agent_apply_usage.json")

    # Cumulative token usage + estimated cost for the Anthropic API calls
    # Score and Tailor make when LLM_PROVIDER=anthropic — see app/scoring_usage.py.
    scoring_usage_path: Path = Path("data/scoring_usage.json")

    claude_cli_path: str = "claude"
    claude_model: str = "sonnet"

    agent_apply_workers: int = 1
    agent_apply_headless: bool = False


settings = Settings()
