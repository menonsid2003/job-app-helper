import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.apply.base import ApplicationAdapter, ApplicationResult, resume_upload_filename
from app.capsolver import CapSolverClient, CapSolverError
from app.schemas import ApplicantProfile

logger = logging.getLogger(__name__)

FIELD_TIMEOUT_MS = 15_000
DECLINE_OPTION_PATTERN = re.compile(r"decline|prefer not|don'?t wish|wish not|choose not", re.IGNORECASE)
EEO_LABEL_PATTERN = re.compile(r"gender|race|ethnicity|veteran|disability", re.IGNORECASE)
SPONSORSHIP_LABEL_PATTERN = re.compile(r"sponsorship", re.IGNORECASE)


class GreenhouseApplicationAdapter(ApplicationAdapter):
    """Only claims Greenhouse's own hosted board domains (job-boards.greenhouse.io,
    boards.greenhouse.io) — NOT company-branded embeds on their own domain
    (e.g. stripe.com/jobs/...?gh_jid=...), since those weren't verified during
    this build and may have different DOM structure around the same
    underlying Greenhouse form. Field selectors below are built from manually
    inspecting one real, live GitLab posting's *unsubmitted* form (accessible
    labels only — never filled with real data or submitted) — verify against
    a second real company before trusting this broadly; Greenhouse's exact
    DOM can vary by account/theme."""

    name = "greenhouse"

    def supports(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host in {"job-boards.greenhouse.io", "boards.greenhouse.io"}

    def submit(
        self,
        page: Page,
        application_url: str,
        profile: ApplicantProfile,
        resume_pdf_path: Path,
        screenshot_dir: Path,
        capsolver: CapSolverClient | None,
    ) -> ApplicationResult:
        if not profile.is_complete():
            return ApplicationResult(status="unsupported", notes="Applicant profile is incomplete (full_name/email/phone required).")

        page.goto(application_url, wait_until="domcontentloaded")

        self._reveal_form_if_needed(page)

        try:
            page.get_by_label("First Name").wait_for(timeout=FIELD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            return ApplicationResult(status="unsupported", notes="Could not locate the application form on this page.")

        first_name, _, last_name = profile.full_name.partition(" ")
        page.get_by_label("First Name").fill(first_name)
        page.get_by_label("Last Name").fill(last_name or first_name)
        page.get_by_label("Email", exact=False).first.fill(profile.email)
        self._fill_if_present(page, "Phone", profile.phone)
        self._fill_if_present(page, "LinkedIn Profile", profile.linkedin_url)

        resume_result = self._upload_resume(page, resume_pdf_path, profile)
        if resume_result is not None:
            return resume_result

        custom_question_result = self._handle_custom_questions(page, profile)
        if custom_question_result is not None:
            return custom_question_result

        self._decline_eeo_questions(page)

        captcha_result = self._solve_captcha_if_present(page, application_url, capsolver)
        if captcha_result is not None:
            return captcha_result

        screenshot_dir.mkdir(parents=True, exist_ok=True)
        pre_submit_path = screenshot_dir / f"pre-submit-{int(time.time())}.png"
        page.screenshot(path=str(pre_submit_path), full_page=True)

        submit_button = page.get_by_role("button", name=re.compile("submit application", re.IGNORECASE))
        try:
            submit_button.wait_for(timeout=FIELD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            return ApplicationResult(
                status="unsupported", notes="Form filled but could not locate the submit button.",
                screenshot_path=str(pre_submit_path),
            )

        submit_button.click()

        try:
            page.wait_for_load_state("networkidle", timeout=FIELD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass  # best-effort — still take the post-submit screenshot below

        post_submit_path = screenshot_dir / f"post-submit-{int(time.time())}.png"
        page.screenshot(path=str(post_submit_path), full_page=True)

        return ApplicationResult(
            status="submitted",
            notes=f"Submitted at {datetime.now(timezone.utc).isoformat()}.",
            screenshot_path=str(post_submit_path),
        )

    def _reveal_form_if_needed(self, page: Page) -> None:
        """Some Greenhouse postings show the form immediately; others (like
        the one this was built against) show a job description with a
        separate "Apply" button that reveals the form. Try the button;
        if it's not there, assume the form is already visible."""
        apply_button = page.get_by_role("button", name=re.compile(r"^apply$", re.IGNORECASE))
        try:
            apply_button.wait_for(timeout=3_000)
            apply_button.click()
        except PlaywrightTimeoutError:
            pass

    def _fill_if_present(self, page: Page, label: str, value: str) -> None:
        if not value:
            return
        field = page.get_by_label(label, exact=False)
        try:
            field.wait_for(timeout=3_000)
            field.fill(value)
        except PlaywrightTimeoutError:
            pass  # field not on this form — fine, it's optional

    def _upload_resume(self, page: Page, resume_pdf_path: Path, profile: ApplicantProfile) -> ApplicationResult | None:
        file_inputs = page.locator('input[type="file"]')
        try:
            count = file_inputs.count()
        except Exception:
            count = 0
        if count == 0:
            return ApplicationResult(status="unsupported", notes="No resume upload field found on this form.")
        # Resume section appears before Cover Letter in the DOM on the form
        # this was built against — .first targets it. Not verified across
        # other Greenhouse accounts/themes.
        # Uploaded under a clean candidate-name filename rather than the
        # on-disk "v{n}.pdf" — that's what the recruiter actually sees.
        file_inputs.first.set_input_files({
            "name": resume_upload_filename(profile),
            "mimeType": "application/pdf",
            "buffer": resume_pdf_path.read_bytes(),
        })
        return None

    def _handle_custom_questions(self, page: Page, profile: ApplicantProfile) -> ApplicationResult | None:
        """Per-job custom screening questions vary and can't be hardcoded
        generically. We only confidently answer the one pattern we know how
        to answer consistently (visa sponsorship, from the applicant
        profile) — anything else recognized as a required custom question
        is left to a human rather than guessed."""
        comboboxes = page.get_by_role("combobox")
        try:
            count = comboboxes.count()
        except Exception:
            count = 0

        for i in range(count):
            combobox = comboboxes.nth(i)
            accessible_name = combobox.get_attribute("aria-label") or ""
            if not accessible_name:
                continue
            if EEO_LABEL_PATTERN.search(accessible_name):
                continue  # handled separately by _decline_eeo_questions

            if SPONSORSHIP_LABEL_PATTERN.search(accessible_name):
                if profile.requires_visa_sponsorship is None:
                    return ApplicationResult(
                        status="unsupported",
                        notes=f"Form asks a sponsorship question ('{accessible_name}') but "
                        "applicant_profile.requires_visa_sponsorship is not configured.",
                    )
                answer = "Yes" if profile.requires_visa_sponsorship else "No"
                self._select_combobox_option(combobox, answer)
                continue

            # Unrecognized custom question (e.g. "How did you hear about
            # us?", "Country of residence") — real Greenhouse forms have
            # these beyond sponsorship/EEO and they vary per job. Don't
            # guess an answer; flag for manual completion instead.
            return ApplicationResult(
                status="unsupported",
                notes=f"Form has a custom question ('{accessible_name}') this adapter doesn't know how to answer.",
            )
        return None

    def _decline_eeo_questions(self, page: Page) -> None:
        """Standard US EEO/VEVRAA voluntary self-identification questions
        (gender, race/ethnicity, veteran status, disability status) — always
        select the decline-to-answer option rather than guessing demographic
        data we were never given."""
        comboboxes = page.get_by_role("combobox")
        try:
            count = comboboxes.count()
        except Exception:
            count = 0

        for i in range(count):
            combobox = comboboxes.nth(i)
            accessible_name = combobox.get_attribute("aria-label") or ""
            if accessible_name and EEO_LABEL_PATTERN.search(accessible_name):
                self._select_combobox_option(combobox, DECLINE_OPTION_PATTERN)

    def _select_combobox_option(self, combobox, option_matcher) -> None:
        """Handles two different real-world patterns: a plain native
        <select> (select_option by matching visible text), and a custom
        ARIA combobox widget — the kind actually observed on the live
        GitLab form this was built against (a text-input-style combobox
        with a separate "Toggle flyout" button, not a native select) —
        where clicking opens a listbox of role="option" elements."""
        try:
            tag_name = combobox.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag_name = ""

        if tag_name == "select":
            self._select_native_option(combobox, option_matcher)
            return

        try:
            combobox.click()
            page = combobox.page
            if isinstance(option_matcher, re.Pattern):
                option = page.get_by_role("option", name=option_matcher)
            else:
                option = page.get_by_role("option", name=option_matcher, exact=False)
            option.first.click(timeout=3_000)
        except PlaywrightTimeoutError:
            logger.warning("Could not select an option for a combobox — leaving it as-is")

    def _select_native_option(self, combobox, option_matcher) -> None:
        try:
            texts = combobox.locator("option").all_inner_texts()
            if isinstance(option_matcher, re.Pattern):
                match = next((t for t in texts if option_matcher.search(t)), None)
            else:
                match = next((t for t in texts if option_matcher.lower() in t.lower()), None)
            if match:
                combobox.select_option(label=match)
            else:
                logger.warning("No matching <option> found for a native select — leaving it as-is")
        except Exception:
            logger.warning("Could not select a native <option> — leaving it as-is")

    def _solve_captcha_if_present(
        self, page: Page, application_url: str, capsolver: CapSolverClient | None
    ) -> ApplicationResult | None:
        recaptcha_frame = page.locator('[data-sitekey]').first
        try:
            has_captcha = recaptcha_frame.count() > 0
        except Exception:
            has_captcha = False
        if not has_captcha:
            return None

        if capsolver is None:
            return ApplicationResult(
                status="unsupported",
                notes="This form has a CAPTCHA and no CapSolver API key is configured (CAPSOLVER_API_KEY).",
            )

        site_key = recaptcha_frame.get_attribute("data-sitekey")
        if not site_key:
            return ApplicationResult(status="unsupported", notes="CAPTCHA detected but could not read its site key.")

        try:
            token = capsolver.solve_recaptcha_v2(application_url, site_key)
        except CapSolverError as exc:
            return ApplicationResult(status="failed", notes=f"CapSolver failed to solve the CAPTCHA: {exc}")

        page.evaluate(
            """(token) => {
                const el = document.getElementById('g-recaptcha-response')
                    || document.querySelector('textarea[name="g-recaptcha-response"]');
                if (el) { el.style.display = 'block'; el.value = token; }
            }""",
            token,
        )
        return None
