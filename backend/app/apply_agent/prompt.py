"""Prompt builder for the AI agent-apply worker.

Builds the instruction prompt handed to the `claude` CLI subprocess, which
uses Playwright MCP tools to fill out a job application in the worker's
Chrome. All personal data comes from CriteriaConfig.applicant_profile
(Settings page) — nothing here is hardcoded per-user.

Deliberate departures from convenience for the sake of safety:
  - No blind account creation/sign-in on third-party sites. A login wall is
    always a stop condition (RESULT:FAILED:login_issue) unless the site
    allows continuing as a guest, OR one of the two narrow carve-outs below
    applies:
      1. Google SSO via an ALREADY-SIGNED-IN session — the worker's Chrome
         profile persists whatever you signed into by hand via Settings ->
         Agent Apply -> "Open Chrome to log in". The agent may click an
         existing account tile on accounts.google.com, but must never type a
         Google email or password itself. Other SSO providers
         (Microsoft/Okta/Auth0/...) stay fully blocked — no persisted
         session flow exists for them.
      2. A reusable signup email/password, if you've filled one in under
         Settings -> Agent Apply -> Applicant Profile. Used only on a site's
         own account-creation form when there's no guest option and no
         Google SSO — never reused as an answer to anything else on the
         page. Leave it blank to keep the old behavior (always stop).
  - No email-verification-code retrieval (no Gmail/email MCP wired up) —
    an email-only apply or a code-gated signup is also a stop condition.
"""

import shutil
from datetime import datetime
from pathlib import Path

from app.models import Job, Resume
from app.schemas import ApplicantProfile, CriteriaConfig, ScoreOut

# Google is handled as a narrow carve-out (see _build_login_section) rather
# than blocked outright — everything else here stays a hard stop since there's
# no persisted-session mechanism for them the way there is for Google.
GOOGLE_SSO_DOMAIN = "accounts.google.com"
BLOCKED_SSO_DOMAINS = [
    "login.microsoftonline.com",
    "login.live.com",
    "okta.com",
    "auth0.com",
    "onelogin.com",
]


def _build_profile_summary(profile: ApplicantProfile, job: Job, score: ScoreOut | None) -> str:
    lines = [
        f"Name: {profile.full_name}",
        f"Email: {profile.email}",
        f"Phone: {profile.phone}",
    ]
    if profile.linkedin_url:
        lines.append(f"LinkedIn: {profile.linkedin_url}")
    if profile.city:
        lines.append(f"City: {profile.city}")

    if profile.work_authorization_note:
        lines.append(f"Work Authorization: {profile.work_authorization_note}")
    elif profile.requires_visa_sponsorship is not None:
        lines.append(
            "Work Authorization: "
            + ("Requires visa sponsorship." if profile.requires_visa_sponsorship else "Does not require visa sponsorship.")
        )
    else:
        lines.append("Work Authorization: Not configured — if asked directly, answer honestly if you can infer it from the profile, otherwise leave the question for manual follow-up (do not guess).")

    if profile.years_of_experience is not None:
        lines.append(f"Years Experience: {profile.years_of_experience}")

    target_role = profile.target_role or job.title
    lines.append(f"Target Role: {target_role}")

    lines.extend([
        "Age 18+: Yes",
        "Background Check: Yes",
        "Previously Worked Here: No",
        "How Heard: Online Job Board",
    ])
    lines.extend([
        "Gender: Decline to self-identify",
        "Race/Ethnicity: Decline to self-identify",
        "Veteran Status: I am not a protected veteran",
        "Disability Status: I do not wish to answer",
    ])

    if score is not None:
        lines.append(f"Internal Fit Score: {score.score}/100 — {score.reasoning}")

    return "\n".join(lines)


def _build_location_check(profile: ApplicantProfile, criteria: CriteriaConfig) -> str:
    primary_city = profile.city or (criteria.locations[0] if criteria.locations else "your area")
    accept_list = ", ".join(criteria.locations) if criteria.locations else primary_city

    return f"""== LOCATION CHECK (do this FIRST before any form) ==
Read the job page. Determine the work arrangement. Then decide:
- "Remote" or "work from anywhere" -> ELIGIBLE. Apply.
- "Hybrid" or "onsite" in {accept_list} -> ELIGIBLE. Apply.
- "Hybrid" or "onsite" in another city BUT the posting also says "remote OK" or "remote option available" -> ELIGIBLE. Apply.
- "Onsite only" or "hybrid only" in any city outside the list above with NO remote option -> NOT ELIGIBLE. Stop immediately. Output RESULT:FAILED:not_eligible_location
- City is overseas with no remote option -> NOT ELIGIBLE. Output RESULT:FAILED:not_eligible_location
- Cannot determine location -> Continue applying. If a screening question reveals it's non-local onsite, answer honestly and let the system reject if needed.
Do NOT fill out forms for jobs that are clearly onsite in a non-acceptable location. Check EARLY, save time."""


def _build_salary_section(profile: ApplicantProfile, criteria: CriteriaConfig) -> str:
    floor = profile.salary_expectation or criteria.salary_min

    if floor is None:
        return """== SALARY (no floor configured) ==
No salary floor is set in your profile or Settings. If the posting shows a range, answer with its MIDPOINT.
If no range is posted and you're asked for a number, write "Negotiable, open to discussing based on total compensation" instead of inventing a figure."""

    hourly = floor // 2080
    return f"""== SALARY (think, don't just copy) ==
${floor} USD is the FLOOR. Never go below it. But don't always use it either.

Decision tree:
1. Job posting shows a range (e.g. "$120K-$160K")? -> Answer with the MIDPOINT.
2. Title says Senior, Staff, Lead, Principal, Architect, or level II/III/IV? -> Treat ${floor} as a soft minimum; use the midpoint of the posted range if it's higher.
3. No salary info anywhere? -> Use ${floor} USD.
4. Asked for a range? -> Give posted midpoint minus 10% to midpoint plus 10%. No posted range? -> "${floor}-${floor + 20000} USD".
5. Hourly rate? -> Divide your annual answer by 2080 (${floor} USD/yr ≈ ${hourly}/hr)."""


def _build_screening_section(profile: ApplicantProfile) -> str:
    city = profile.city or "their city"
    years = profile.years_of_experience if profile.years_of_experience is not None else "multiple"
    target_role = profile.target_role or "the target role"

    return f"""== SCREENING QUESTIONS (be strategic) ==
Hard facts -> answer truthfully from the profile above. No guessing. This includes:
  - Location/relocation: lives in {city}, cannot relocate
  - Work authorization: answer from the profile only
  - Citizenship, clearance, licenses, certifications: answer from profile only
  - Criminal/background: answer from profile only

Skills and tools -> be confident. This candidate is targeting {target_role} roles with {years} years of experience. If asked "Do you have experience with [tool]?" and it's in the same domain, answer YES. Don't sell short.

Open-ended questions ("Why do you want this role?", "Tell us about yourself") -> Write 2-3 sentences. Be specific to THIS job. Reference something from the job description. Connect it to a real achievement from the resume. No generic fluff.

EEO/demographics -> "Decline to self-identify" or "Prefer not to say" for everything.

If a required question can't be answered truthfully or confidently from the profile/resume above, and it's not covered by a rule here -> leave the form incomplete and output RESULT:FAILED:unanswerable_question rather than guessing at something factual."""


def _build_hard_rules(profile: ApplicantProfile) -> str:
    work_auth_rule = "Work auth: Answer truthfully from the profile above. Never guess or fabricate."
    return f"""== HARD RULES (never break these) ==
1. Never lie about: citizenship, work authorization, criminal history, education credentials, security clearance, licenses.
2. {work_auth_rule}
3. Name: Legal name = {profile.full_name}. Use exactly this on every form."""


def _build_login_section(profile: ApplicantProfile) -> str:
    if profile.signup_email and profile.signup_password:
        signup_rule = (
            f"Use email {profile.signup_email} and password {profile.signup_password} on the site's OWN "
            f"signup/login form to create (or, if it says that email is already registered, sign into) an "
            f"account. Never enter this email/password anywhere else on the page."
        )
    else:
        signup_rule = "No reusable signup email/password is configured (Settings -> Agent Apply) -> STOP. Output RESULT:FAILED:login_issue."

    return f"""== LOGIN WALLS ==
5a. Check the URL. If you land on {', '.join(BLOCKED_SSO_DOMAINS)}, or any other SSO/OAuth page OTHER than {GOOGLE_SSO_DOMAIN} -> STOP. Output RESULT:FAILED:sso_required. Do NOT attempt to sign in.
5b. Landing on {GOOGLE_SSO_DOMAIN} is allowed, ONLY to pick an account tile from an already-signed-in session (this worker's Chrome profile persists whatever Google account was signed into by hand ahead of time). If an account tile is offered, click it and continue. If instead Google asks you to type an email or password (no session found) -> STOP. Output RESULT:FAILED:login_issue. Never type a Google email or password yourself.
5c. Check for popups: browser_tabs action "list". If a login popup opened, browser_tabs action "select" to switch to it and check its URL too — apply 5a/5b to whatever that popup shows.
5d. If the site lets you continue as a guest / apply without an account -> do that.
5e. If the site REQUIRES creating an account or signing in with a password, and there's no guest option and 5b didn't apply -> {signup_rule}
5f. After any login/signup click: run CAPTCHA DETECT. Login pages frequently have invisible CAPTCHAs. If found, solve it, then re-evaluate 5a-5e."""


def build_prompt(
    job: Job,
    resume: Resume,
    score: ScoreOut | None,
    profile: ApplicantProfile,
    criteria: CriteriaConfig,
    worker_dir: Path,
    capsolver_api_key: str | None,
    dry_run: bool = False,
) -> str:
    """Build the full instruction prompt for one job application.

    worker_dir is the AI agent's per-worker scratch directory (already reset
    for this job) — the resume gets copied there under a clean, recruiter-
    facing filename before the path is handed to the agent for upload.
    """
    src_pdf = Path(resume.pdf_path).resolve()
    if not src_pdf.exists():
        raise ValueError(f"Resume PDF not found: {src_pdf}")

    name_slug = profile.full_name.replace(" ", "_") or "Applicant"
    upload_pdf = worker_dir / f"{name_slug}_Resume.pdf"
    shutil.copy(str(src_pdf), str(upload_pdf))

    resume_text = Path(resume.ats_text_path).read_text(encoding="utf-8")

    profile_summary = _build_profile_summary(profile, job, score)
    location_check = _build_location_check(profile, criteria)
    salary_section = _build_salary_section(profile, criteria)
    screening_section = _build_screening_section(profile)
    hard_rules = _build_hard_rules(profile)
    login_section = _build_login_section(profile)
    captcha_section = _build_captcha_section(capsolver_api_key)

    phone_digits = "".join(c for c in profile.phone if c.isdigit())
    application_url = job.canonical_url or job.source_url

    if dry_run:
        submit_instruction = "IMPORTANT: Do NOT click the final Submit/Apply button. Review the form, verify all fields, then output RESULT:APPLIED with a note that this was a dry run."
    else:
        submit_instruction = "BEFORE clicking Submit/Apply, take a snapshot and review EVERY field on the page. Verify all data matches the APPLICANT PROFILE and RESUME — name, email, phone, location, work auth, resume uploaded. If anything is wrong or missing, fix it FIRST. Only click Submit after confirming everything is correct."

    return f"""You are an autonomous job application agent. Your ONE mission: get this candidate an interview. Think strategically. Act decisively. Submit the application.

== JOB ==
URL: {application_url}
Title: {job.title}
Company: {job.company}

== FILES ==
Resume PDF (upload this): {upload_pdf}

== RESUME TEXT (use when filling text fields) ==
{resume_text}

== APPLICANT PROFILE ==
{profile_summary}

== YOUR MISSION ==
Submit a complete, accurate application. Use the profile and resume as source data -- adapt to fit each form's format.

If something unexpected happens and these instructions don't cover it, figure it out yourself within the HARD RULES and NEVER DO THESE below. The goal is always the same: submit the application, or stop cleanly with the right RESULT code.

{hard_rules}

== NEVER DO THESE (immediate RESULT:FAILED if encountered) ==
- NEVER grant camera, microphone, screen sharing, or location permissions -> RESULT:FAILED:unsafe_permissions
- NEVER do video/audio verification, selfie capture, ID photo upload, or biometric anything -> RESULT:FAILED:unsafe_verification
- NEVER set up a freelancing/contractor-marketplace profile (Mercor, Toptal, Upwork, Fiverr, Turing, etc.) -> RESULT:FAILED:not_a_job_application
- NEVER agree to hourly/contract rates, availability calendars, or "set your rate" flows. Applying for FULL-TIME salaried positions only.
- NEVER install browser extensions, download executables, or run assessment software.
- NEVER enter payment info, bank details, or SSN/SIN.
- NEVER click "Allow" on any browser permission popup. Always deny/block.
- NEVER create an account or sign in with a password anywhere, except the two narrow carve-outs in LOGIN WALLS below (an already-signed-in Google session, or the configured reusable signup email/password) — and even then, only on that site's own login/signup form.
- If the site is NOT a job application form (profile builder, skills marketplace, talent network signup, coding assessment platform) -> RESULT:FAILED:not_a_job_application

{location_check}

{salary_section}

{screening_section}

== STEP-BY-STEP ==
1. browser_navigate to the job URL.
2. browser_snapshot to read the page. Run CAPTCHA DETECT (see CAPTCHA section). If found, solve it before continuing.
3. LOCATION CHECK. Read the page for location info. If not eligible, output RESULT and stop.
4. Find and click the Apply button. If the page says "email resume to X" with no on-site form -> RESULT:FAILED:manual_email_required (this pipeline doesn't send email).
   After clicking Apply: browser_snapshot. Run CAPTCHA DETECT.
{login_section}
6. Upload resume. ALWAYS upload fresh -- delete any existing resume first, then browser_file_upload with the PDF path above.
7. Check ALL pre-filled fields. ATS systems parse your resume and auto-fill -- it's often WRONG.
   - "Current Job Title" / "Most Recent Title" -> use the title from the RESUME TEXT, NOT whatever the parser guessed.
   - Compare every other field to the APPLICANT PROFILE. Fix mismatches. Fill empty fields.
8. Answer screening questions using the rules above.
9. {submit_instruction}
10. After submit: browser_snapshot. Run CAPTCHA DETECT. Then check for new tabs (browser_tabs action: "list"). Switch to newest, close old. Snapshot to confirm submission. Look for "thank you" or "application received".
11. Output your result.

== RESULT CODES (output EXACTLY one) ==
RESULT:APPLIED -- submitted successfully
RESULT:EXPIRED -- job closed or no longer accepting applications
RESULT:CAPTCHA -- blocked by unsolvable captcha
RESULT:LOGIN_ISSUE -- login/account wall with no guest option
RESULT:FAILED:not_eligible_location -- onsite outside acceptable area, no remote option
RESULT:FAILED:reason -- any other failure (brief reason)

== BROWSER EFFICIENCY ==
- browser_snapshot ONCE per page to understand it. Then use browser_take_screenshot to check results (10x less memory).
- Only snapshot again when you need element refs to click/fill.
- Multi-page forms (Workday, Taleo, iCIMS): snapshot each new page, fill all fields, click Next/Continue. Repeat until final review page.
- Fill ALL fields in ONE browser_fill_form call. Not one at a time.
- Keep your thinking SHORT. Don't repeat page structure back.
- CAPTCHA AWARENESS: After any navigation, Apply/Submit/Login click, or when a page feels stuck -- run CAPTCHA DETECT. Invisible CAPTCHAs show NO visual widget but block submissions silently.

== FORM TRICKS ==
- Popup/new window opened? browser_tabs action "list" to see all tabs. browser_tabs action "select" with the tab index to switch.
- "Upload your resume" pre-fill page (Workday, Lever, etc.): NOT the application form yet. Click "Select file", browser_file_upload with the resume PDF path. Wait for parsing. Click Next/Continue.
- File upload not working? Try: (1) browser_click the upload button/area, (2) browser_file_upload with the path.
- Dropdown won't fill? browser_click to open it, then browser_click the option.
- Checkbox won't check via fill_form? Use browser_click on it instead. Snapshot to verify.
- Phone field with country prefix: just type digits {phone_digits}
- Date fields: {datetime.now().strftime('%m/%d/%Y')}
- Validation errors after submit? Take BOTH snapshot AND screenshot. Snapshot shows text errors, screenshot shows red-highlighted fields. Fix all, retry.
- Honeypot fields (hidden, "leave blank"): skip them.

{captcha_section}

== WHEN TO GIVE UP ==
- Same page after 3 attempts with no progress -> RESULT:FAILED:stuck
- Job is closed/expired/page says "no longer accepting" -> RESULT:EXPIRED
- Page is broken/500 error/blank -> RESULT:FAILED:page_error
Stop immediately. Output your RESULT code. Do not loop."""


def _build_captcha_section(capsolver_api_key: str | None) -> str:
    key_line = capsolver_api_key or "NOT CONFIGURED — skip to MANUAL FALLBACK for all CAPTCHAs"

    return f"""== CAPTCHA ==
You solve CAPTCHAs via the CapSolver REST API. No browser extension. You control the entire flow.
API key: {key_line}
API base: https://api.capsolver.com

CRITICAL RULE: When ANY CAPTCHA appears (hCaptcha, reCAPTCHA, Turnstile), you MUST:
1. Run CAPTCHA DETECT to get the type and sitekey
2. Run CAPTCHA SOLVE (createTask -> poll -> inject) with the CapSolver API
3. ONLY go to MANUAL FALLBACK if CapSolver returns errorId > 0

--- CAPTCHA DETECT ---
Run this browser_evaluate after every navigation, Apply/Submit/Login click, or when a page feels stuck.

browser_evaluate function: () => {{{{
  const r = {{}};
  const url = window.location.href;
  const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
  if (hc) {{{{ r.type = 'hcaptcha'; r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey; }}}}
  if (!r.type && document.querySelector('script[src*="hcaptcha.com"], iframe[src*="hcaptcha.com"]')) {{{{
    const el = document.querySelector('[data-sitekey]');
    if (el) {{{{ r.type = 'hcaptcha'; r.sitekey = el.dataset.sitekey; }}}}
  }}}}
  if (!r.type) {{{{
    const cf = document.querySelector('.cf-turnstile, [data-turnstile-sitekey]');
    if (cf) {{{{ r.type = 'turnstile'; r.sitekey = cf.dataset.sitekey || cf.dataset.turnstileSitekey; }}}}
  }}}}
  if (!r.type && document.querySelector('script[src*="challenges.cloudflare.com"]')) {{{{
    r.type = 'turnstile_script_only'; r.note = 'Wait 3s and re-detect.';
  }}}}
  if (!r.type) {{{{
    const s = document.querySelector('script[src*="recaptcha"][src*="render="]');
    if (s) {{{{ const m = s.src.match(/render=([^&]+)/); if (m && m[1] !== 'explicit') {{{{ r.type = 'recaptchav3'; r.sitekey = m[1]; }}}} }}}}
  }}}}
  if (!r.type) {{{{
    const rc = document.querySelector('.g-recaptcha');
    if (rc) {{{{ r.type = 'recaptchav2'; r.sitekey = rc.dataset.sitekey; }}}}
  }}}}
  if (!r.type && document.querySelector('script[src*="recaptcha"]')) {{{{
    const el = document.querySelector('[data-sitekey]');
    if (el) {{{{ r.type = 'recaptchav2'; r.sitekey = el.dataset.sitekey; }}}}
  }}}}
  if (r.type) {{{{ r.url = url; return r; }}}}
  return null;
}}}}

Result actions:
- null -> no CAPTCHA. Continue normally.
- "turnstile_script_only" -> browser_wait_for time: 3, re-run detect.
- Any other type -> proceed to CAPTCHA SOLVE below.

--- CAPTCHA SOLVE ---
STEP 1 -- CREATE TASK:
browser_evaluate function: async () => {{{{
  const r = await fetch('https://api.capsolver.com/createTask', {{{{
    method: 'POST', headers: {{{{'Content-Type': 'application/json'}}}},
    body: JSON.stringify({{{{ clientKey: '{capsolver_api_key or ""}', task: {{{{ type: 'TASK_TYPE', websiteURL: 'PAGE_URL', websiteKey: 'SITE_KEY' }}}} }}}})
  }}}});
  return await r.json();
}}}}

TASK_TYPE values: hcaptcha -> HCaptchaTaskProxyLess, recaptchav2 -> ReCaptchaV2TaskProxyLess, recaptchav3 -> ReCaptchaV3TaskProxyLess, turnstile -> AntiTurnstileTaskProxyLess.
PAGE_URL = the url from detect result. SITE_KEY = the sitekey from detect result.
Response: {{"errorId": 0, "taskId": "abc123"}} on success. If errorId > 0 -> MANUAL FALLBACK.

STEP 2 -- POLL (replace TASK_ID with the taskId from step 1):
Loop: browser_wait_for time: 3, then:
browser_evaluate function: async () => {{{{
  const r = await fetch('https://api.capsolver.com/getTaskResult', {{{{
    method: 'POST', headers: {{{{'Content-Type': 'application/json'}}}},
    body: JSON.stringify({{{{ clientKey: '{capsolver_api_key or ""}', taskId: 'TASK_ID' }}}})
  }}}});
  return await r.json();
}}}}
- "processing" -> wait 3s, poll again. Max 10 polls (30s).
- "ready" -> extract token: reCAPTCHA/hCaptcha: solution.gRecaptchaResponse. Turnstile: solution.token.
- errorId > 0 or 30s timeout -> MANUAL FALLBACK.

STEP 3 -- INJECT TOKEN (replace THE_TOKEN with actual token string):
For reCAPTCHA v2/v3:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{{{ el.value = token; el.style.display = 'block'; }}}});
  return 'injected';
}}}}
For hCaptcha:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  const ta = document.querySelector('[name="h-captcha-response"], textarea[name*="hcaptcha"]');
  if (ta) ta.value = token;
  return 'injected';
}}}}
For Turnstile:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  const inp = document.querySelector('[name="cf-turnstile-response"], input[name*="turnstile"]');
  if (inp) inp.value = token;
  return 'injected';
}}}}

After injecting: browser_wait_for time: 2, then snapshot.
- Widget gone or green check -> success. Click Submit if needed.
- No change -> click Submit/Verify/Continue button.
- Still stuck -> token may have expired (~2 min). Re-run from STEP 1.

--- MANUAL FALLBACK ---
Only reach this if CapSolver createTask returned errorId > 0.
1. Audio challenge: look for "audio"/"accessibility" button.
2. Simple text/logic puzzles -> solve them yourself.
3. All else fails -> RESULT:CAPTCHA."""
