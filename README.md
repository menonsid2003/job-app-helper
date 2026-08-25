# Job Application Helper

A self-hosted tool that automates the boring parts of a job search: it finds
postings, scores them against your criteria with an LLM, filters out ones
that don't match your work authorization or location, and helps you tailor
your resume per job. Built as a personal project to get hands-on experience
with AI-assisted development and LLM-driven applications.

## What it does

- **Discover** — polls Greenhouse, Lever, and Workday company job boards, plus
  optional broader search (Indeed, LinkedIn, ZipRecruiter, ...) via JobSpy.
- **Score** — one LLM call per listing returns a fit score, reasoning, and
  work-authorization/location eligibility.
- **Filter** — hard-excludes jobs that require citizenship, a security
  clearance, explicitly refuse sponsorship, or aren't in your target
  country — before you ever see them.
- **Review** — a dashboard to mark jobs Pursue / Skip / Snooze, with an
  Excluded tab to sanity-check the filters aren't over-triggering.
- **Tailor** — sends your resume + a job description to the LLM for a single
  rewrite pass, with a line-level diff so you can see exactly what changed
  before using it.
- **Track** — a status table (tailored → applied → interview → offer) for
  jobs you're actively pursuing.
- **Auto-apply** *(optional, off by default)* — fills out Greenhouse/Lever
  application forms automatically. See "Known limitations" before turning
  this on.

## Quickstart (Docker)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Get an API key from [console.anthropic.com](https://console.anthropic.com)
   (used for LLM scoring/tailoring — pay-as-you-go, cost per job scored is
   small).
3. From the project root:
   ```bash
   cp .env.example .env
   cp backend/criteria.example.yaml backend/criteria.yaml
   ```
   Open `.env` and paste your key into `ANTHROPIC_API_KEY=`.
4. Start it:
   ```bash
   docker compose up --build
   ```
5. Open **http://localhost:5173**, go to **Settings → Base Resume**, and
   paste your resume as plain text. Then set your target roles, locations,
   and companies (also in Settings).
6. Click **"Run Full Pipeline"** on the dashboard to discover and score jobs.

To stop: `docker compose down`. To start again later: `docker compose up`.

Full walkthrough, troubleshooting, and what's off by default and why:
see [SETUP.md](SETUP.md).

## Manual setup (without Docker)

<details>
<summary>Backend</summary>

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY, or point at a local Ollama instead
uvicorn app.main:app --reload
```

Optional extras: `pip install --no-deps "python-jobspy>=1.1.80"` for the
JobSpy connector (needs `--no-deps`, see comment in `requirements.txt` for
why), `playwright install chromium` if you'll use auto-apply.
</details>

<details>
<summary>Frontend</summary>

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open http://localhost:5173.
</details>

## Configuration

Everything — target roles, keywords, locations, salary floor, companies to
poll, auto-apply toggle — is editable from the app's **Settings** page, no
file editing required. `backend/criteria.yaml` is where it's persisted if
you'd rather hand-edit it directly.

**LLM provider**: defaults to the Claude API. To use a local model instead
(free, needs your own [Ollama](https://ollama.com) server), set in
`backend/.env`:
```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```
Switching back and forth is just this env var and a restart. Note: smaller
local models (like `llama3.1:8b`) have been observed occasionally fabricating
work-authorization flags on job postings that don't actually mention them —
worth spot-checking results either way, but especially if you stay on a
small local model.

## Known limitations

- **Auto-apply has not been tested against live job postings** — only
  against local HTML fixtures. It's designed to fail safe (skip/flag
  unsupported rather than guess) rather than submit something wrong, but
  read `app/apply/` before trusting it with real applications.
- **CAPTCHA-solving (CapSolver) is wired in but unverified** against a real
  API key or real CAPTCHA.
- **No database migrations yet** — a schema change means deleting
  `backend/data/job_app_helper.db` and losing tracked jobs/notes.
- **Greenhouse/Lever/Workday discovery needs a maintained company list** —
  none of the three support cross-company search, so you add employers
  yourself (Settings → "Target companies to poll").
- **US-location filtering has one known edge case**: a bare list of city
  names mixing a US city with a non-US one (e.g. "Toronto, Atlanta, or
  Chicago") can get excluded on the non-US match. Rare in practice — check
  the Excluded tab occasionally.
- **Resume tailoring has no cross-check beyond the diff view** — the LLM is
  prompted not to invent experience, but nothing enforces that besides the
  prompt. Always read the diff before sending a tailored resume anywhere.
