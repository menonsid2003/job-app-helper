# Quickstart (for a friend running their own copy)

This gets you a working local instance in about 10 minutes: Docker, one API
key, paste your resume, done. Everything else (target roles, companies,
locations, auto-apply, etc.) is editable later from the app's Settings page —
no file editing required beyond the two copy steps below.

For what the app actually does and its known limitations, see
[README.md](README.md) — this file is just setup steps.

## 1. Install Docker Desktop

https://www.docker.com/products/docker-desktop/ — Windows, Mac, or Linux.
Open it once after installing so its background service is running.

## 2. Get a Claude API key

This app scores every job it finds with one LLM call, and Claude API is the
default provider for this setup (no local model install required). Get a key
at https://console.anthropic.com — you'll need a payment method on file, but
cost per job scored is small (Haiku pricing). You can watch usage at any time
in the Anthropic Console.

## 3. Copy the two config templates

From the project root:

```bash
cp .env.example .env
cp backend/criteria.example.yaml backend/criteria.yaml
```

Open `.env` and paste your key in:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Neither `.env` nor `backend/criteria.yaml` are checked into git (see
`.gitignore`) — they're your personal copies now.

## 4. Start it

```bash
docker compose up --build
```

First build takes a few minutes. Once it's running, open
**http://localhost:5173**.

To stop: Ctrl+C, or `docker compose down` from another terminal. To start
again later: `docker compose up` (no `--build` needed unless you changed
code).

## 5. Add your resume

In the app, go to **Settings → Base Resume** and paste your resume as plain
text (no tables/columns/graphics — this goes straight into LLM prompts). Hit
Save. That's the only thing the app truly needs from you before it's useful.

## 6. Set your search criteria

Still in Settings: target roles, keywords, preferred locations, salary
floor, and role categories are all editable there. The template ships with a
starter list of tech companies (Greenhouse/Lever) under "Target companies to
poll" — add/remove tokens to match employers you actually want tracked, or
replace the whole list if you're not in tech (see README.md's "Setup"
section for how to find a company's Greenhouse/Lever token).

If you're job-hunting outside the US, edit `country_only` and the
`exclude_location_keywords`/work-authorization sections too — the template
defaults assume a US-based, sponsorship-sensitive search.

## 7. Run it

From the dashboard, click **"Run Full Pipeline (Discover → Score →
Auto-Apply)"** (auto-apply itself stays off unless you separately enable it
in Settings — this just runs Discover + Score). Watch the live log, then
check the Review Queue for scored jobs.

## What's off by default, and why

- **Auto-apply** (real application submissions with no per-job approval) —
  off. Read README.md's "Auto-apply" section, including its caveat that the
  adapters have never been run against a live posting, before turning it on.
- **CAPTCHA solving (CapSolver)** — needs its own paid API key in `.env`,
  and per README has never been verified against a real CAPTCHA in this
  build. Only relevant if you turn auto-apply on.
- **Agent-Apply** (a Claude-CLI-driven browser agent that fills out
  applications) — this opens a real, visible Chrome window and needs the
  `claude` CLI installed on the machine running it. It's not practical to
  run inside Docker (no GUI); use the manual/local setup path in README.md
  instead if you want to try it.
- **JobSpy** (Indeed/LinkedIn/etc. search) and **Google Sheets tracking** —
  both opt-in toggles in Settings, off until you turn them on.

## If something goes wrong

- `docker compose logs backend` / `docker compose logs frontend` — see what
  each container is actually doing.
- Blank job list after running the pipeline: check the Excluded tab — your
  criteria (location/work-auth filters especially) may be excluding
  everything. Loosen them in Settings and re-run.
- Schema errors after pulling an update: there's no DB migration tooling
  yet — delete `backend/data/job_app_helper.db` and restart (you'll lose
  tracked jobs/notes).
