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


class LeverApplicationAdapter(ApplicationAdapter):
    """Only claims Lever's own hosted posting domain (jobs.lever.co) — not
    company-branded embeds on their own domain, same scoping caveat as the
    Greenhouse adapter. Field selectors are built from Lever's documented
    public application form structure (name/email/phone/urls[LinkedIn]/resume
    fields, standard EEO self-identification selects) — like the Greenhouse
    adapter when it was first built, this has NOT been verified against a
    real, live Lever posting, only local HTML fixtures. Expect to need
    adjustment the first time it meets a real form; treat "submitted" results
    from this adapter with a bit more scrutiny than Greenhouse's until then."""

    name = "lever"

    def supports(self, url: str) -> bool:
        return urlparse(url).netloc.lower() == "jobs.lever.co"

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

        try:
            page.get_by_label("Full name", exact=False).wait_for(timeout=FIELD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            return ApplicationResult(status="unsupported", notes="Could not locate the application form on this page.")

        page.get_by_label("Full name", exact=False).fill(profile.full_name)
        page.get_by_label("Email", exact=False).first.fill(profile.email)
        self._fill_if_present(page, "Phone", profile.phone)
        self._fill_if_present(page, "LinkedIn", profile.linkedin_url)

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
            pass

        post_submit_path = screenshot_dir / f"post-submit-{int(time.time())}.png"
        page.screenshot(path=str(post_submit_path), full_page=True)

        return ApplicationResult(
            status="submitted",
            notes=f"Submitted at {datetime.now(timezone.utc).isoformat()}.",
            screenshot_path=str(post_submit_path),
        )

    def _fill_if_present(self, page: Page, label: str, value: str) -> None:
        if not value:
            return
        field = page.get_by_label(label, exact=False)
        try:
            field.wait_for(timeout=3_000)
            field.fill(value)
        except PlaywrightTimeoutError:
            pass

    def _upload_resume(self, page: Page, resume_pdf_path: Path, profile: ApplicantProfile) -> ApplicationResult | None:
        file_inputs = page.locator('input[type="file"]')
        try:
            count = file_inputs.count()
        except Exception:
            count = 0
        if count == 0:
            return ApplicationResult(status="unsupported", notes="No resume upload field found on this form.")
        # Uploaded under a clean candidate-name filename rather than the
        # on-disk "v{n}.pdf" — that's what the recruiter actually sees.
        file_inputs.first.set_input_files({
            "name": resume_upload_filename(profile),
            "mimeType": "application/pdf",
            "buffer": resume_pdf_path.read_bytes(),
        })
        return None

    def _handle_custom_questions(self, page: Page, profile: ApplicantProfile) -> ApplicationResult | None:
        """Same principle as the Greenhouse adapter: only sponsorship is
        answered confidently from the applicant profile; anything else
        recognized as a required custom question is left to a human."""
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

            if re.search(r"sponsorship", accessible_name, re.IGNORECASE):
                if profile.requires_visa_sponsorship is None:
                    return ApplicationResult(
                        status="unsupported",
                        notes=f"Form asks a sponsorship question ('{accessible_name}') but "
                        "applicant_profile.requires_visa_sponsorship is not configured.",
                    )
                answer = "Yes" if profile.requires_visa_sponsorship else "No"
                self._select_combobox_option(combobox, answer)
                continue

            return ApplicationResult(
                status="unsupported",
                notes=f"Form has a custom question ('{accessible_name}') this adapter doesn't know how to answer.",
            )
        return None

    def _decline_eeo_questions(self, page: Page) -> None:
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
