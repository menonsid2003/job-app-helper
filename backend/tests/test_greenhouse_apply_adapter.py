from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app.apply.greenhouse import GreenhouseApplicationAdapter
from app.schemas import ApplicantProfile

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_url(name: str) -> str:
    return (FIXTURES_DIR / name).resolve().as_uri()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page()
    yield p
    p.close()


@pytest.fixture
def resume_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4\n%fake resume for testing\n")
    return path


@pytest.fixture
def screenshot_dir(tmp_path: Path) -> Path:
    return tmp_path / "screenshots"


def _complete_profile() -> ApplicantProfile:
    return ApplicantProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-1234",
        linkedin_url="https://linkedin.com/in/janedoe",
        requires_visa_sponsorship=True,
    )


# ---- supports() ----


def test_supports_greenhouse_hosted_domains():
    adapter = GreenhouseApplicationAdapter()
    assert adapter.supports("https://job-boards.greenhouse.io/gitlab/jobs/123")
    assert adapter.supports("https://boards.greenhouse.io/acme/jobs/456")


def test_does_not_support_other_domains():
    adapter = GreenhouseApplicationAdapter()
    assert not adapter.supports("https://stripe.com/jobs/search?gh_jid=123")
    assert not adapter.supports("https://jobs.lever.co/acme/abc")


# ---- submit() happy path ----


def test_submit_fills_standard_fields_and_submits(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form.html"),
        profile=_complete_profile(),
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "submitted"
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).exists()
    assert "Thanks for applying" in page.content()


def test_submit_answers_sponsorship_question_from_profile(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    profile = _complete_profile()
    profile.requires_visa_sponsorship = True

    # Fill everything up to submit, then check the sponsorship select's value
    # before the button click swaps out the DOM.
    page.goto(_fixture_url("greenhouse_form.html"))
    page.get_by_label("First Name").fill(profile.full_name.split()[0])
    page.get_by_label("Last Name").fill(profile.full_name.split()[1])
    page.get_by_label("Email", exact=False).first.fill(profile.email)
    adapter._handle_custom_questions(page, profile)

    assert page.locator("#sponsorship").input_value() == "yes"


def test_submit_declines_eeo_questions(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    page.goto(_fixture_url("greenhouse_form.html"))
    adapter._decline_eeo_questions(page)

    assert page.locator("#gender").input_value() == "decline"
    assert page.locator("#veteran").input_value() == "decline"


def test_submit_clicks_apply_button_to_reveal_form(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    profile = ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")

    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form_needs_apply_click.html"),
        profile=profile,
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "submitted"


# ---- safety fallbacks (unsupported, never guesses) ----


def test_returns_unsupported_for_unrecognized_custom_question(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form_unrecognized_question.html"),
        profile=_complete_profile(),
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "unsupported"
    assert "How did you hear about us?" in result.notes
    # Never clicked submit — page should still show the form, not the fixture's thank-you text.
    assert "Thanks for applying" not in page.content()


def test_returns_unsupported_for_sponsorship_question_when_profile_unconfigured(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    profile = ApplicantProfile(full_name="Jane Doe", email="jane@example.com", phone="555-1234")
    assert profile.requires_visa_sponsorship is None

    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form.html"),
        profile=profile,
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "unsupported"
    assert "sponsorship" in result.notes.lower()


def test_returns_unsupported_for_incomplete_applicant_profile(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    incomplete_profile = ApplicantProfile(full_name="", email="", phone="")

    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form.html"),
        profile=incomplete_profile,
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "unsupported"
    assert "profile is incomplete" in result.notes.lower()


def test_returns_unsupported_when_no_form_found(page, resume_pdf, screenshot_dir, tmp_path):
    blank_page = tmp_path / "blank.html"
    blank_page.write_text("<html><body><p>No form here.</p></body></html>", encoding="utf-8")

    adapter = GreenhouseApplicationAdapter()
    result = adapter.submit(
        page=page,
        application_url=blank_page.resolve().as_uri(),
        profile=_complete_profile(),
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "unsupported"
    assert "could not locate" in result.notes.lower()


# ---- CAPTCHA handling ----


def test_returns_unsupported_when_captcha_present_and_no_capsolver_configured(page, resume_pdf, screenshot_dir):
    adapter = GreenhouseApplicationAdapter()
    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form_with_captcha.html"),
        profile=_complete_profile(),
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=None,
    )

    assert result.status == "unsupported"
    assert "capsolver" in result.notes.lower()
    # Check the *rendered* DOM, not raw page.content() source — the fixture's
    # own <script> text literally contains the string "Thanks for applying",
    # which would make a naive content() substring check pass even if the
    # form was never actually submitted.
    assert page.locator("#application_form").is_visible()


def test_solves_captcha_and_submits_when_capsolver_configured(page, resume_pdf, screenshot_dir):
    import httpx
    from app.capsolver import CapSolverClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        return httpx.Response(
            200, json={"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "fake-solved-token"}}
        )

    capsolver = CapSolverClient("fake-key", transport=httpx.MockTransport(handler))

    adapter = GreenhouseApplicationAdapter()
    result = adapter.submit(
        page=page,
        application_url=_fixture_url("greenhouse_form_with_captcha.html"),
        profile=_complete_profile(),
        resume_pdf_path=resume_pdf,
        screenshot_dir=screenshot_dir,
        capsolver=capsolver,
    )

    assert result.status == "submitted"
    assert "fake-solved-token" in page.content()
