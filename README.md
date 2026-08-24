# Job Application Helper

Self-hosted job discovery + LLM scoring pipeline with a work-authorization-
and location-aware filter (F1 OPT / H-1B sponsorship + US-only constraints),
Greenhouse + Lever + Workday connectors, canonical-link resolution, LLM
resume tailoring with a diff view, an opt-in Greenhouse auto-apply adapter,
GPU-window-based overnight scheduling, and a React dashboard covering the
whole Discover → Score → Review → Tailor → Apply → Track pipeline.
Covers **all 8 phases** of the original build plan, with real caveats on
Phase 7 (auto-apply) — see "Known limitations" below before relying on it.

## What's here

- **Discover**: `GreenhouseConnector`, `LeverConnector`, and
  `WorkdayConnector` poll a configured list of company board tokens/career
  site URLs per connector (none of the three have cross-company search, so
  this is a maintained list — see `criteria.yaml` → `target_companies`,
  keyed by connector name). All three are gated together by
  `company_board_connectors_enabled` (default `true`) — turn it off to run
  JobSpy-only discovery without touching or losing your `target_companies`
  list, then back on to resume polling it. `JobSpyConnector` (opt-in, off by
  default — `criteria.yaml` → `jobspy.enabled`) additionally searches general
  job boards (Indeed, LinkedIn, ZipRecruiter, Glassdoor, ...) via the
  [JobSpy](https://github.com/speedyapply/JobSpy) library, one search per
  target-role × preferred-location combination — see "Connector coverage".
- **Score**: each relevant listing gets one LLM call (Ollama, `llama3.1:8b`
  by default — or the Claude API, see "LLM provider" below) returning score,
  reasoning, matched/missing keywords, role category, and
  work-authorization + location-eligibility fields.
- **Work-auth filtering**: a cheap keyword prefilter runs before any LLM
  call; everything else gets `hard_exclude` computed from the LLM's
  structured output as `citizenship_required OR security_clearance_required
  OR sponsorship_mentioned == "no"`. Sponsorship simply not being mentioned
  is never a reason to exclude.
- **US-only location filtering**: same two-pass shape as work-auth, plus a
  third, more reliable signal where available. Lever provides a real ISO
  country code per posting (`country_hint`) — used as authoritative when
  present. Otherwise a cheap keyword blocklist (`exclude_location_keywords`)
  rejects obvious non-US locations before any LLM call, never rejecting a
  posting that also lists an explicit US option (e.g. "Dublin, US-Remote"
  stays eligible). Genuinely ambiguous locations (bare "Remote", blank) are
  left for the LLM to judge from the full job description.
- **Relevance filtering**: word-boundary matched title/keyword gates (e.g.
  `exclude_keywords: ["intern"]` won't also match "international"), plus a
  `prefer_full_time` soft scoring preference (contract still scored, just
  weighted a little lower, all else equal).
- **Live pipeline progress + control**: the dashboard shows current step,
  running counts, and a scrolling log while a run is in progress, and a
  **Stop** button that halts the run gracefully after the in-flight job
  finishes.
- **Criteria editing UI**: edit target roles, keywords, weights, location
  rules, and target companies from Settings — no manual YAML editing
  required (though you still can). A **Rescore All** button re-runs scoring
  for every job whose latest score was computed under an older criteria
  version, without re-discovering.
- **Review Queue actions**: Pursue / Skip / Snooze buttons on the main jobs
  table.
- **Tracking Table**: jobs marked "Pursue" (and beyond — tailored/applied/
  interview/rejected/offer) land in a dedicated table with an editable
  status dropdown and notes field — the primary daily-use screen once
  you're actively applying.
- **Excluded tab**: sanity-check that the work-auth/location filters aren't
  over-triggering.
- **Canonical link resolution**: after a job scores at/above
  `canonical_link_score_threshold`, `resolve_canonical_link` probes the
  Greenhouse and Lever public APIs directly (title fuzzy-matched via
  `rapidfuzz.token_set_ratio`, threshold 90) to find the company's own
  career-site link. Currently mostly a no-op in practice — Greenhouse/
  Lever/Workday board links already *are* the canonical company-hosted
  link — but it's wired in and ready for a future non-canonical source
  (e.g. a scraped aggregator) without further plumbing.
- **Resume tailoring**: from the Tracking table, "Tailor Resume" sends your
  base resume + the job description to the LLM for a single rewrite pass
  (`app/resume_tailor.py`), with a strict "don't invent experience, only
  reorder/reword/re-emphasize what's already there" system prompt. Every
  version is saved with a line-level diff (`app/resume_diff.py`,
  `difflib.SequenceMatcher`) — **the diff view, not the prompt, is the real
  safety net**: always read it before using a tailored resume, since a small
  local model can still overstate things despite the prompt. Versions are
  downloadable as PDF (reportlab) or plain ATS text.
- **Auto-apply (Greenhouse + Lever, off by default)**: `GreenhouseApplicationAdapter`
  and `LeverApplicationAdapter` (Playwright) fill the standard fields, upload
  the tailored resume, answer the sponsorship-pattern question from your
  `applicant_profile`, and **decline to answer and return `unsupported` for
  any custom question they don't specifically recognize** (including EEO
  race/gender/veteran-status questions, which they explicitly skip rather
  than guess) — neither invents personal or demographic data. Gated by two
  separate things: the `auto_apply_enabled` toggle in Settings, and a
  separate manual "Run Auto-Apply" trigger on the new Auto-Apply Log page —
  it is never run by the overnight scheduler. Every attempt is logged with
  a before/after-submit screenshot, viewable from the Auto-Apply Log table.
  `_apply_to_one_job` picks `job.canonical_url or job.source_url` — so a
  JobSpy-sourced job (Indeed/LinkedIn/ZipRecruiter/...) that canonical-link
  resolution matched back to its original Greenhouse or Lever posting
  applies through that original listing automatically, never through the
  aggregator's own "quick apply" flow. **LinkedIn/Indeed/ZipRecruiter/
  Glassdoor submission automation was deliberately not built** — doing so
  would mean logging into your account on those platforms with stored
  credentials and defeating anti-bot protection built specifically to catch
  that pattern, which isn't something this project takes on regardless of
  ToS-risk tolerance (unlike the JobSpy *search* reversal above, this one
  isn't a risk-preference call). A JobSpy job with no canonical-link match
  falls back to `source_url` and comes back `unsupported` — same as any
  other platform with no adapter, apply manually via the Tracking table.
- **GPU-window scheduling**: an optional nightly auto-run
  (`auto_schedule_enabled` + `gpu_schedule_window`, e.g. `22:00-07:00`) using
  APScheduler, checked every 30 minutes. This is a time-window check only —
  no actual GPU load probing — since Ollama runs on a separate LAN machine
  this app can't introspect. It only ever triggers the discover+score
  pipeline, never auto-apply.

## Setup

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install --no-deps "python-jobspy>=1.1.80"   # see note below — only needed for the JobSpy connector
# (the quotes matter: unquoted, both bash and PowerShell parse ">=1.1.80" as an output redirect, not a version spec)
playwright install chromium   # only needed if you'll use auto-apply
cp .env.example .env     # edit if your Ollama host/model differ
```

`python-jobspy` must be installed separately with `--no-deps`, as shown above
— its published metadata hard-pins `NUMPY==1.26.3` exactly, a version with no
wheel for Python 3.13+/3.14, so a plain `pip install -r requirements.txt`
including it tries to compile numpy from source and fails without a C/C++
toolchain. `requirements.txt` already lists jobspy's actual runtime
dependencies (pandas, beautifulsoup4, markdownify, regex, requests,
tls-client) directly with modern, wheel-available versions, so `--no-deps`
just skips jobspy's own broken pin — nothing it needs is missing.

If you plan to use auto-apply's CAPTCHA-solving fallback, set
`CAPSOLVER_API_KEY` in `.env` (get a key from capsolver.com — this project
does not include one and the integration has not been verified against a
real key/real CAPTCHA in this build). Auto-apply also requires
`applicant_profile` (full name, email, phone — set in Settings) to be
complete before it will run at all.

Set your base resume — no file editing needed: once the app is running, open
**Settings → Base Resume** and paste your resume as plain text (ATS-friendly —
no tables/columns/graphics). It's saved via `PUT /api/resume/base` to
`backend/resume/base_resume.txt`, the same file `load_base_resume_text()`
reads directly into every scoring prompt. (You can still drop the file there
by hand instead, e.g. for scripted deployments — same path either way.)
`backend/resume/` is gitignored and not backed up anywhere — keep a copy of
the real resume elsewhere too.

Everything else that defines "what job field this is" is also editable from
**Settings**, with no YAML required: target roles, locations, keywords, salary
floor, and **role categories** (the closed set the LLM sorts each job into —
defaults to `ServiceNow`/`SWE`/`Full Stack`/`Data Engineer`, replace with
whatever fits, e.g. `ICU`/`ER`/`Med-Surg` for nursing; `Other` is always an
implicit fallback). `backend/criteria.yaml` is the file all of that is
persisted to, and can still be hand-edited directly if you prefer — but a
friend you're deploying this for doesn't need to touch it. The one exception
is `target_companies` (Greenhouse/Lever/Workday board tokens/URLs) — also
editable from Settings, but it ships with starter lists of 18 Greenhouse + 9
Lever + 6 Workday tech companies that only make sense for a tech job search;
for a different field, either clear those out and rely on JobSpy instead (see
"JobSpy" above — toggle `company_board_connectors_enabled` off), or replace
them with that field's own company boards.

Run it:

```bash
uvicorn app.main:app --reload
```

Run tests:

```bash
pytest
```

**If you pull schema changes** (new columns on `Job`, e.g. `notes`/
`last_updated`): there's no migration tooling yet (Alembic was deliberately
deferred for Phase 1 simplicity), so delete `backend/data/job_app_helper.db`
and let it recreate on next startup. You'll lose tracked jobs/notes when you
do this — a real migration story is worth adding before this matters for
real usage.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env   # points at http://localhost:8000 by default
npm run dev
```

Open http://localhost:5173.

### Docker

`docker-compose.yml` is included for later deployment into your Dockge stack,
but hasn't been run/tested in this build session (no Docker CLI available
here). Sanity-check it locally before trusting it:

```bash
docker compose up --build
```

## Known limitations

- **Greenhouse/Lever discovery requires a maintained company list.** Neither
  has cross-company search. Add/remove tokens in `criteria.yaml` (or via
  Settings) as you find employers worth tracking. Lever has noticeably fewer
  current public users than Greenhouse — the seeded starter list is smaller
  (9 vs. 18) as a result.
- **Saving criteria via the Settings UI strips YAML comments.** `save_criteria`
  round-trips through `CriteriaConfig.model_dump()` → `yaml.safe_dump`, which
  has no concept of the explanatory comments in the original file. Once you
  save from the UI once, any comments you'd written by hand are gone from
  that point on. Not a data-loss issue (all values persist correctly), just
  a documentation one.
- **US-only location filtering has one known residual edge case.** The
  blocklist + LLM two-pass classifier was validated against ~750 real,
  distinct Greenhouse location strings collected from the seeded companies
  (see `matches_non_us_location`/`has_us_signal` in
  `backend/app/scoring/prefilter.py`) and correctly resolves the vast
  majority, including tricky real cases like "CA-Toronto" (Canada, not
  California), "Berlin, DE" (Germany, not Delaware — DE/Delaware and
  AR/Arkansas were both real collisions found and fixed), and "Dublin,
  US-Remote" (correctly still eligible via the US option). The one case it
  still gets wrong: a bare list of city names with no state/country
  qualifier that mixes a US city with a blocklisted non-US city — e.g.
  "Toronto, Atlanta, or Chicago" gets excluded on the "Toronto" match even
  though Atlanta/Chicago are valid US options. Seen once in ~750 samples.
  Lever postings sidestep this entirely via `country_hint` (a real ISO
  country code) when available. Check the Excluded tab occasionally for
  this pattern on Greenhouse results.
- **llama3.1:8b's judgment has a real ceiling — including hallucinating
  hard-exclude flags.** Live testing during this build (against the real
  Ollama instance) surfaced several concrete issues:
  - *Fixed*: the relevance prefilter originally let any job whose description
    happened to mention a `nice_to_have_keyword` (e.g. "AWS", "Python")
    through to scoring — which let completely unrelated roles (an HR "Team
    Member Relations Partner" posting) reach the LLM just because of shared
    company boilerplate mentioning your tech stack. Fixed by gating only on
    the target-role head noun in the title (`engineer`/`developer`/
    `administrator`) plus `must_have_keywords`.
  - *Fixed*: the LLM sometimes returns a string like `"not_mentioned"` in a
    boolean field (`citizenship_required`) instead of `true`/`false`, which
    crashed Pydantic validation and silently dropped the job from scoring
    entirely. `WorkAuthorization`/`ScoreResult` now coerce leniently instead
    of crashing.
  - *Not fixable via prompting — a real model-capability ceiling*: the model
    has been observed setting `citizenship_required: true` on ordinary
    engineering roles with reasoning text that doesn't even mention
    citizenship — a fabricated hard-exclude flag with no basis in the actual
    posting. This is a correctness risk in the *dangerous* direction (hiding
    a genuinely good job, not just showing an ineligible one). See "LLM
    provider: Ollama vs. Claude API" below — an `AnthropicProvider` is now
    built as a drop-in alternative for exactly this failure mode.

## LLM provider: Ollama vs. Claude API

`LLMProvider` (`backend/app/llm/base.py`) is an interface with two
implementations — `OllamaProvider` (local, free, current default) and
`AnthropicProvider` (Claude API, paid, no GPU dependency, doesn't fabricate
flags the way `llama3.1:8b` was observed to). `make_default_provider()`
(`backend/app/llm/factory.py`) picks between them based on a single setting,
so switching back and forth is a `.env` edit and a backend restart — no code
change either direction.

```bash
# backend/.env

# Local Ollama (default):
LLM_PROVIDER=ollama
OLLAMA_HOST=http://192.168.50.6:11434
OLLAMA_MODEL=llama3.1:8b

# Claude API instead:
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5
```

If `ANTHROPIC_API_KEY` is left blank here, the `anthropic` SDK still checks
the `ANTHROPIC_API_KEY` environment variable itself — exporting it instead
of putting it in `.env` also works.

**On model choice:** `ANTHROPIC_MODEL` defaults to `claude-opus-5`. Given
this app makes one call per scored job, cost/latency scale with pipeline
size — for a large `target_companies` list, a cheaper model
(`claude-haiku-4-5`) is worth benchmarking against Opus's accuracy for this
specific classification/extraction task before committing to running it at
scale; nothing else in the app needs to change to try that, just the env
var. This has not been benchmarked in this build — no real `ANTHROPIC_API_KEY`
was available in this session, so `AnthropicProvider` is verified only by
unit tests (`backend/tests/test_anthropic_provider.py`) that mock the SDK
client. **Run at least one real pipeline scoring pass against your own key
before trusting it over Ollama for hard-exclude decisions** — verify it
doesn't reproduce the same fabricated-citizenship-requirement pattern
`llama3.1:8b` showed, and check the actual Anthropic Console usage/cost
after a full run to confirm it lines up with expectations.

**If you stay on Ollama**, `llama3.1:8b` is workable but its
instruction-following is inconsistent enough to occasionally fabricate
work-authorization flags (see above) and merge distinct role categories.
Pick a bigger local model based on your GPU's VRAM:

| VRAM | Try | Notes |
|---|---|---|
| ~8GB | `qwen2.5:7b-instruct` | Similar size to llama3.1:8b, generally better structured-output reliability in community benchmarks |
| ~12–16GB | `qwen2.5:14b-instruct` (recommended starting point) | Noticeably stronger instruction-following/JSON reliability at a size that still runs comfortably alongside gaming workloads |
| ~12–16GB (alt.) | `phi4:14b` | Microsoft's Phi-4 — strong reasoning-for-size, another solid option at this tier |
| 24GB+ | `qwen2.5:32b-instruct` | Meaningfully stronger; slower per call |

To try one: `ollama pull qwen2.5:14b-instruct`, then set
`OLLAMA_MODEL=qwen2.5:14b-instruct` in `backend/.env` and restart the
backend. Worth spot-checking a few of the same jobs that got weird results
here (fabricated citizenship requirements, inconsistent role categories) to
see if the new model handles them better before committing to a switch.

## Connector coverage decisions (Phase 4)

- **Built**: Greenhouse, Lever, Workday. All three either have a documented
  public integration API (Greenhouse, Lever) or a robots.txt that explicitly
  *allows* crawling their job listings and a usable semi-public JSON API
  (Workday — confirmed live: `Allow: /Jobs/`, `Disallow: /refreshFacet/`
  only). `WorkdayConnector` pre-filters by title before fetching full job
  detail (search and detail are separate calls on Workday, unlike
  Greenhouse/Lever's single bulk-content fetch — without this, a large
  employer like NVIDIA at 2000+ open postings would mean thousands of HTTP
  calls per run) and caps total records scanned per company at 300.
- LinkedIn and Indeed, via `jobspy`.`JobSpyConnector`
  (`app/connectors/jobspy_connector.py`) wraps the `jobspy` library and is
  wired into `default_connectors()`, covering Indeed/LinkedIn/ZipRecruiter/
  Glassdoor/Google/Bayt/Naukri. It ships **off by default**
  (`criteria.yaml` → `jobspy.enabled: false`) — enabling it is a decision left to you, made
  explicitly per-deployment rather than defaulted on.

## Phase 5-8 limitations and what was intentionally not done

- **`GreenhouseApplicationAdapter` has only been verified against local
  HTML fixtures**, built to match the real structure of one live Greenhouse
  form (GitLab's) observed during research, plus edge cases (native
  `<select>` vs. custom ARIA comboboxes, a captcha present, an "Apply"
  button that must be clicked before the form reveals itself, and an
  unrecognized custom question). **It has never been run against a real,
  live Greenhouse posting** — per your instruction, Phase 7 was built and
  unit/fixture-tested only, with no live-site submission testing. Expect to
  find real-world form variations it doesn't yet handle; it's designed to
  fail safe (`status="unsupported"`) rather than guess when it hits
  something unrecognized, but "fails safe" isn't the same as "handles
  everything."
- **Auto-apply covers Greenhouse and Lever.** Workday application forms
  have no adapter — jobs from that source, or any JobSpy job whose
  canonical-link resolution didn't find a Greenhouse/Lever match, still need
  manual application; `run_auto_apply` simply skips any job it has no
  adapter for. `LeverApplicationAdapter` (`app/apply/lever.py`) has the same
  "never verified against a real, live posting" caveat as
  `GreenhouseApplicationAdapter` below — fixture-tested only.
- **CapSolver integration (`app/capsolver.py`) is implemented against their
  documented task-based API but has never been exercised against a real API
  key or a real CAPTCHA.** If you hit a captcha during a real auto-apply
  run without a working `CAPSOLVER_API_KEY`, expect it to fail rather than
  solve.
- **Resume tailoring is a single LLM pass with no cross-check beyond the
  diff view.** `llama3.1:8b` (or whatever model you've swapped in) is asked
  not to invent experience, but nothing in the pipeline enforces that
  besides the prompt — always read the diff before sending a tailored
  resume anywhere.
- **`jobspy` search (discovery) was later reversed and is now built** — see
  "Reversed (was 'Declined')" in "Connector coverage decisions" above; 
  Why it's a different question than the search
  reversal: **submitting applications through LinkedIn/Indeed/ZipRecruiter/
  Glassdoor's own flow** (as opposed to reading their public listing pages)
  CapSolver *is* wired into the Greenhouse/Lever auto-apply adapters.

All 8 phases from the original build plan are now implemented. Nothing
further is planned unless you want to extend adapter coverage (Workday
auto-apply), add a real migration story (Alembic) before this holds
meaningful production data, or revisit Dice if a legitimate discovery path
turns up.
