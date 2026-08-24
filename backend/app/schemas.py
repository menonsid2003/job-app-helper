from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models import JobStatus

# llama3.1:8b (and small local models generally) don't reliably respect strict
# JSON typing — e.g. it has been observed putting "not_mentioned" (meant for
# sponsorship_mentioned) into the citizenship_required/security_clearance_required
# boolean fields. Rather than let a validation error drop the whole score,
# coerce leniently with a safe (non-excluding) default for anything unrecognized.


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "required", "1"}
    return value


def _coerce_sponsorship(value: Any) -> Any:
    if not isinstance(value, str):
        return "not_mentioned"
    lowered = value.strip().lower()
    if lowered in {"yes", "no", "not_mentioned"}:
        return lowered
    if lowered in {"true", "available", "offered", "offers sponsorship"}:
        return "yes"
    if lowered in {"false", "refused", "not offered", "unable to sponsor", "will not sponsor", "no sponsorship"}:
        return "no"
    return "not_mentioned"


# ---- Connector output (not persisted directly) ----


class JobListing(BaseModel):
    source: str
    source_url: str
    title: str
    company: str
    location: str = ""
    salary_text: str | None = None
    description: str
    posted_date: str | None = None
    country_hint: str | None = Field(
        default=None,
        description="ISO 2-letter country code, when the source platform provides one directly "
        "(e.g. Lever's 'country' field). Authoritative over text-based location parsing when present.",
    )
    is_remote_hint: bool | None = Field(
        default=None,
        description="Remote flag, when the source platform provides one directly as structured data "
        "(e.g. jobspy's own 'is_remote' column) rather than only as wording inside the location text. "
        "Authoritative over text-based remote detection (app/location_parse.py) when present — this is "
        "what catches a listing like 'San Francisco, CA' that jobspy itself flags remote, where the "
        "location string alone would otherwise say nothing about remote status at all.",
    )


# ---- Criteria config (parsed from criteria.yaml) ----


class WorkAuthCriteria(BaseModel):
    exclude_citizenship_required: bool = True
    exclude_security_clearance_required: bool = True
    exclude_if_sponsorship_explicitly_refused: bool = True
    hard_exclude_prefilter_keywords: list[str] = Field(default_factory=list)


class ScoringWeights(BaseModel):
    title_match: float = 0.3
    skills_overlap: float = 0.3
    location_fit: float = 0.15
    seniority_fit: float = 0.15
    salary_fit: float = 0.10


class ApplicantProfile(BaseModel):
    """Real personal data needed to fill out application forms — deliberately
    blank by default. Auto-apply refuses to run (see auto_apply orchestrator)
    until full_name/email/phone are filled in; nothing here is guessed or
    fabricated on your behalf."""

    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    requires_visa_sponsorship: bool | None = Field(
        default=None,
        description="Answers the common 'will you require sponsorship' screening question consistently. "
        "None means unconfigured — a form with this question will be treated as unsupported (flagged for "
        "manual completion) rather than guessed.",
    )

    # Optional, additive fields — not needed by the deterministic Greenhouse/
    # Lever adapters (which only fill what a form's own fields ask for), but
    # give the AI agent apply path (app/apply_agent/) more to work with when
    # answering open-ended screening questions confidently instead of
    # flagging them unsupported. Safe to leave blank.
    city: str = ""
    target_role: str = Field(
        default="", description="Falls back to the job's own title when blank."
    )
    years_of_experience: int | None = None
    salary_expectation: int | None = Field(
        default=None, description="Annual salary floor, USD. Falls back to Settings' salary_min when blank."
    )
    work_authorization_note: str = Field(
        default="",
        description="Free-text override for how the agent should answer work-authorization screening "
        "questions, e.g. 'US citizen, no sponsorship ever needed' or 'H-1B, transfer required'. Falls back "
        "to a generic phrasing derived from requires_visa_sponsorship when blank.",
    )

    # Some ATSes (Workday in particular) require creating a site account
    # before you can even see the application form, with no guest option.
    # Left blank by default, the agent still refuses those (see
    # app/apply_agent/prompt.py) rather than inventing a login. Filling
    # these in opts into letting it create an account with this one reused
    # email/password when a site demands it. Stored in plaintext in
    # criteria.yaml, same as the rest of this profile — don't reuse a
    # password that matters elsewhere.
    signup_email: str = Field(
        default="", description="Reused when a site requires creating an account with no guest option."
    )
    signup_password: str = Field(
        default="", description="Reused alongside signup_email. Plaintext in criteria.yaml — use a dedicated password."
    )

    def is_complete(self) -> bool:
        return bool(self.full_name and self.email and self.phone)


class JobSpyCriteria(BaseModel):
    """Config for the JobSpy connector (github.com/speedyapply/JobSpy), which
    scrapes general job boards rather than calling a per-company API. Off by
    default — unlike Greenhouse/Lever/Workday this hits sites that can rate-limit
    or block scraping, so it's opt-in. When enabled, it runs one search per
    (target_roles x locations) combination."""

    enabled: bool = False
    sites: list[str] = Field(
        default_factory=lambda: ["indeed", "linkedin", "zip_recruiter"],
        description="JobSpy site names to search: indeed, linkedin, zip_recruiter, glassdoor, google, bayt, naukri.",
    )
    results_wanted: int = Field(default=20, description="Max results requested per site, per search.")
    hours_old: int = Field(default=72, description="Only include postings at most this many hours old.")
    country_indeed: str = Field(
        default="USA", description="Country filter used by JobSpy's Indeed/Glassdoor search."
    )


class GoogleSheetsCriteria(BaseModel):
    """Opt-in: append a row to a personal Google Sheet whenever a job's
    status becomes "applied" — from the Tracking table, auto-apply, or
    agent-apply alike (see app/google_sheets.py). Off by default. The
    credential itself (a service-account JSON key) is never entered here —
    see Settings.google_sheets_credentials_path — since this belongs with
    the other secrets in .env, not in criteria.yaml or the web form."""

    enabled: bool = False
    spreadsheet_url: str = Field(
        default="",
        description="Full Google Sheets URL (or a bare spreadsheet ID). The sheet must be shared as Editor "
        "with the service account's client_email from the credentials JSON, or every push will fail.",
    )
    sheet_name: str = Field(
        default="",
        description="Tab name to append rows to. Blank uses the spreadsheet's first/default sheet.",
    )


class CriteriaConfig(BaseModel):
    role_categories: list[str] = Field(
        default_factory=lambda: ["ServiceNow", "SWE", "Full Stack", "Data Engineer"],
        description="Closed set of buckets the LLM sorts each scored job into (shown as the 'Role category' "
        "column). Field-specific — replace these with categories that make sense for your own target roles "
        "(e.g. ['ICU', 'ER', 'Med-Surg'] for nursing). 'Other' is always accepted as a fallback on top of "
        "this list and doesn't need to be included here.",
    )
    target_roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    country_only: list[str] = Field(default_factory=lambda: ["US"])
    seniority: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    must_have_keywords: list[str] = Field(default_factory=list)
    nice_to_have_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    prefer_full_time: bool = Field(
        default=True,
        description="Full-time roles preferred; contract is acceptable but should generally score a bit "
        "lower than an equivalent full-time role, all else equal. Does not hard-exclude contract roles — "
        "use exclude_keywords for a hard exclude (e.g. internships).",
    )
    work_authorization: WorkAuthCriteria = Field(default_factory=WorkAuthCriteria)
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    auto_apply_enabled: bool = False
    gpu_schedule_window: str | None = None
    auto_schedule_enabled: bool = Field(
        default=False,
        description="If true, automatically trigger a pipeline run once during each occurrence of "
        "gpu_schedule_window (e.g. once per night). Off by default — setting gpu_schedule_window alone "
        "does not turn on automatic runs; this flag does. No GPU-load probing is done (Ollama runs on a "
        "separate machine on your LAN with no monitoring agent) — this is purely a time-window gate, the "
        "spec's stated fallback when GPU load can't be checked directly.",
    )
    canonical_link_score_threshold: int = Field(
        default=70,
        description="Only attempt canonical-link resolution for scored (non-excluded) jobs at or above this "
        "score. A no-op today — Greenhouse/Lever/Workday links are already canonical — but active for any "
        "future non-canonical (board) source.",
    )
    target_companies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Company tokens to poll per connector, keyed by connector name "
        "(e.g. {'greenhouse': ['stripe', ...], 'lever': ['palantir', ...]}). Each connector "
        "has no cross-company search of its own, so this is how it knows what to poll.",
    )
    company_board_connectors_enabled: bool = Field(
        default=True,
        description="Master switch for Greenhouse/Lever/Workday together — the three connectors that poll "
        "a maintained target_companies list rather than searching broadly. Turn off to run JobSpy-only "
        "discovery (broad search, no company list to maintain) without losing or editing target_companies; "
        "turn back on to resume polling it. On by default, matching prior behavior.",
    )
    exclude_location_keywords: list[str] = Field(
        default_factory=list,
        description="Non-US countries/cities/region codes to reject on sight (hard constraint, mirrors "
        "work_authorization.hard_exclude_prefilter_keywords). Never applied if the location string also "
        "contains an explicit US signal (e.g. 'Dublin, US-Remote' is still eligible).",
    )
    applicant_profile: ApplicantProfile = Field(default_factory=ApplicantProfile)
    jobspy: JobSpyCriteria = Field(default_factory=JobSpyCriteria)
    google_sheets: GoogleSheetsCriteria = Field(default_factory=GoogleSheetsCriteria)


# ---- LLM scoring output ----


class WorkAuthorization(BaseModel):
    citizenship_required: bool = False
    security_clearance_required: bool = False
    sponsorship_mentioned: Literal["yes", "no", "not_mentioned"] = "not_mentioned"
    hard_exclude: bool = False

    @field_validator("citizenship_required", "security_clearance_required", "hard_exclude", mode="before")
    @classmethod
    def _lenient_bool(cls, value: Any) -> Any:
        return _coerce_bool(value)

    @field_validator("sponsorship_mentioned", mode="before")
    @classmethod
    def _lenient_sponsorship(cls, value: Any) -> Any:
        return _coerce_sponsorship(value)


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100)
    reasoning: str
    matched_keywords: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    role_category: str = "Other"
    red_flags: list[str] = Field(default_factory=list)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    # Only reached for ambiguous locations (e.g. bare "Remote") that the cheap
    # keyword prefilter couldn't classify — the LLM reads the full description,
    # which often states eligibility even when the location field doesn't.
    # Defaults True (inclusive) when the description doesn't clarify, matching
    # the same "silence isn't grounds for exclusion" philosophy as sponsorship.
    location_us_eligible: bool = True
    # The connector-supplied location text is often silent or misleading on
    # remote status (see app/location_parse.py) — the LLM reads the full
    # description, the same source a human would actually trust. True/False
    # when the description states a clear policy either way, None when it
    # genuinely doesn't say — deliberately NOT defaulted to either bool the
    # way location_us_eligible is, since a guessed value here would silently
    # overwrite Job.is_remote with something no more reliable than what it
    # already had.
    is_remote: bool | None = None

    @field_validator("location_us_eligible", mode="before")
    @classmethod
    def _lenient_location_us_eligible(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            # Inclusive by default: anything other than a clear negative
            # (including "unclear"/""/typos) is treated as eligible.
            return value.strip().lower() not in {"false", "no", "not eligible", "non-us", "0"}
        return True if value is None else bool(value)

    @field_validator("is_remote", mode="before")
    @classmethod
    def _lenient_is_remote(cls, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "remote"}:
                return True
            if lowered in {"false", "no", "not remote", "on-site", "onsite", "in-office", "hybrid"}:
                return False
            return None  # "unclear", "not specified", "", typos, etc.
        return None


# ---- API responses ----


class ScoreOut(BaseModel):
    score: int
    reasoning: str
    matched_keywords: list[str]
    missing_requirements: list[str]
    role_category: str
    red_flags: list[str]
    work_authorization: dict
    scored_at: datetime
    model_used: str

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: int
    source: str
    source_url: str
    canonical_url: str | None
    title: str
    company: str
    location: str
    is_remote: bool | None
    salary_text: str | None
    posted_date: str | None
    first_seen: datetime
    status: JobStatus
    notes: str
    last_updated: datetime
    latest_score: ScoreOut | None = None

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    description: str


class JobUpdate(BaseModel):
    """PATCH payload for user-driven changes — the Review Queue actions
    (pursue/skip/snooze), Tracking Table edits (status/notes), and manually
    filling in company/location when a source (e.g. jobspy scrapes) left
    them blank, purely for trackability — never re-derived or validated
    against the posting. Deliberately can't set discovered/scored/excluded —
    those are pipeline-owned.

    is_remote is genuinely tri-state (True/False/None-as-"unknown"), unlike
    the other fields here, so a submitted null has to mean something
    different from the field being omitted — the router checks
    "is_remote" in update.model_fields_set rather than `is not None` to
    tell "set it back to unknown" apart from "didn't touch this field"."""

    status: Literal[
        "pursue", "skip", "snoozed", "tailored", "applied", "interview", "rejected", "offer"
    ] | None = None
    notes: str | None = None
    company: str | None = None
    location: str | None = None
    is_remote: bool | None = None


class ResumeOut(BaseModel):
    id: int
    job_id: int
    version: int
    diff_summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BaseResumeOut(BaseModel):
    text: str


class BaseResumeUpdate(BaseModel):
    text: str


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    job_title: str
    job_company: str
    resume_id: int | None
    status: Literal["submitted", "failed", "unsupported"]
    method: Literal["auto", "manual", "agent"]
    notes: str
    screenshot_path: str | None
    submitted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AutoApplyStatusOut(BaseModel):
    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    logs: list[str]
    error: str | None
    stop_requested: bool
    submitted_count: int
    failed_count: int
    unsupported_count: int


class FullPipelineStatusOut(BaseModel):
    """Status of a Full Pipeline run (app/full_pipeline.py): discover -> score
    -> promote (score >= score_threshold -> Pursue) -> auto-apply. phase is
    "" until the run actually starts, then one of "discover_score"/"promote"/
    "auto_apply". cost_usd_this_run is a live estimate (see
    app/scoring_usage.py) of Claude API spend during just this run, not a
    real-time account balance."""

    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    logs: list[str]
    error: str | None
    stop_requested: bool
    phase: str
    phase_started_at: datetime | None
    discovered: int
    deduped_skipped: int
    scored: int
    excluded: int
    skipped_low_relevance: int
    promoted_count: int
    score_threshold: int
    submitted_count: int
    failed_count: int
    unsupported_count: int
    cost_usd_this_run: float


class AgentApplyStatusOut(BaseModel):
    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    logs: list[str]
    error: str | None
    stop_requested: bool
    submitted_count: int
    failed_count: int
    unsupported_count: int
    total_cost_usd: float


class AgentApplyUsageOut(BaseModel):
    """Cumulative agent-apply usage across all runs (not reset per-run —
    see app/agent_apply_usage.py), plus the account's last-seen 5-hour
    rate-limit window status. rate_limit_status is None until at least one
    agent-apply job has run since this was added."""

    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    total_cost_usd: float
    job_count: int
    rate_limit_status: str | None
    rate_limit_type: str | None
    rate_limit_resets_at: datetime | None
    last_updated: datetime | None


class ScoringUsageOut(BaseModel):
    """Cumulative token usage + estimated cost for Score and Tailor's
    Anthropic API calls (see app/scoring_usage.py). Only reflects real
    spend when LLM_PROVIDER=anthropic — Ollama scoring/tailoring is free
    and isn't tracked here. total_cost_usd is an estimate against current
    list pricing, not a live balance read from your Anthropic account."""

    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    total_cost_usd: float
    call_count: int
    last_updated: datetime | None


class TailorAllStatusOut(BaseModel):
    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    logs: list[str]
    error: str | None
    stop_requested: bool
    tailored_count: int
    failed_count: int


class LocationBackfillStatusOut(BaseModel):
    """Status of a location-backfill run (app/location_backfill.py) — re-
    fetches jobs with a blank location from their source connector and fills
    in location/is_remote when the posting is still live there."""

    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    logs: list[str]
    error: str | None
    stop_requested: bool
    updated_count: int
    not_found_count: int
    skipped_count: int


class PipelineRunResult(BaseModel):
    discovered: int
    deduped_skipped: int
    scored: int
    excluded: int
    skipped_low_relevance: int
    errors: list[str] = Field(default_factory=list)


class PipelineStatusOut(BaseModel):
    status: Literal["idle", "running", "done", "error", "stopped"]
    started_at: datetime | None
    finished_at: datetime | None
    current_step: str
    discovered: int
    deduped_skipped: int
    scored: int
    excluded: int
    skipped_low_relevance: int
    logs: list[str]
    error: str | None
    stop_requested: bool = False
