from pathlib import Path

import pytest
from pydantic import ValidationError

from app.llm.base import LLMProvider
from app.resume_tailor import (
    EntryBlock,
    ResumeContent,
    ResumeSection,
    SkillLine,
    build_tailoring_prompt,
    flatten_resume_content,
    render_resume_pdf,
    tailor_resume_content,
)


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0
        self.last_system = None
        self.last_user = None

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return self.response


MINIMAL_CONTENT = {
    "full_name": "Jamie Rivera",
    "phone": "+1 555-0100",
    "email": "jamie@example.com",
    "links": ["linkedin.com/in/jamierivera"],
    "summary": "Data engineer with 4 years of pipeline experience.",
    "sections": [
        {
            "title": "EXPERIENCE",
            "entries": [
                {
                    "heading_left": "Acme Corp",
                    "heading_right": "Jan 2022 - Present",
                    "subheading_left": "Data Engineer",
                    "subheading_right": "Remote",
                    "bullets": ["Built ETL pipelines using Python.", "Owned nightly batch jobs."],
                }
            ],
            "plain_bullets": [],
            "skill_lines": [],
        },
        {"title": "CERTIFICATIONS", "entries": [], "plain_bullets": ["AWS Certified Developer"], "skill_lines": []},
        {
            "title": "SKILLS",
            "entries": [],
            "plain_bullets": [],
            "skill_lines": [{"label": "Languages", "items": "Python, SQL"}],
        },
    ],
}


def test_build_tailoring_prompt_includes_resume_and_job_details():
    system, user = build_tailoring_prompt("MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.")
    assert "MY BASE RESUME TEXT" in user
    assert "Data Engineer" in user
    assert "Acme" in user
    assert "Build pipelines." in user
    assert "invent" in system.lower()


def test_build_tailoring_prompt_includes_experience_bank_when_provided():
    _, user = build_tailoring_prompt(
        "MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.",
        experience_bank_text="OLDER ROLE: Freelance Data Analyst, 2019-2020",
    )
    assert "OLDER ROLE: Freelance Data Analyst, 2019-2020" in user
    assert "experience bank" in user.lower()


def test_build_tailoring_prompt_includes_correction_and_previous_version_when_provided():
    _, user = build_tailoring_prompt(
        "MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.",
        previous_tailored_text="John Doe\n\nLed the crunch project as team lead.",
        correction="Remove the team lead claim, I was not actually the lead.",
    )
    assert "Led the crunch project as team lead." in user
    assert "Remove the team lead claim, I was not actually the lead." in user
    assert "previous tailored version" in user.lower()


def test_build_tailoring_prompt_omits_correction_section_when_blank():
    _, user = build_tailoring_prompt("MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.")
    assert "previous tailored version" not in user.lower()


def test_build_tailoring_prompt_omits_experience_bank_section_when_blank():
    _, user = build_tailoring_prompt("MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.")
    assert "experience bank" not in user.lower()

    _, user_whitespace_only = build_tailoring_prompt(
        "MY BASE RESUME TEXT", "Data Engineer", "Acme", "Build pipelines.", experience_bank_text="   \n  "
    )
    assert "experience bank" not in user_whitespace_only.lower()


def test_tailor_resume_content_returns_validated_model():
    provider = FakeLLMProvider(MINIMAL_CONTENT)
    result = tailor_resume_content(provider, "base resume", "Data Engineer", "Acme", "job description")
    assert isinstance(result, ResumeContent)
    assert result.full_name == "Jamie Rivera"
    assert result.sections[0].entries[0].heading_left == "Acme Corp"
    assert provider.calls == 1


def test_tailor_resume_content_retries_on_malformed_output_then_succeeds():
    provider = FakeLLMProvider({"wrong_field": "oops"})
    # First two calls return the bad shape, force a third good one by swapping mid-flight.
    calls = {"n": 0}
    original = provider.complete_json

    def flaky(system, user):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"wrong_field": "oops"}
        return MINIMAL_CONTENT

    provider.complete_json = flaky
    result = tailor_resume_content(provider, "base resume", "Data Engineer", "Acme", "job description", max_attempts=3)
    assert result.full_name == "Jamie Rivera"
    assert calls["n"] == 3
    provider.complete_json = original


def test_tailor_resume_content_raises_after_exhausting_retries():
    provider = FakeLLMProvider({"wrong_field": "oops"})
    with pytest.raises(ValidationError):
        tailor_resume_content(provider, "base resume", "Data Engineer", "Acme", "job description", max_attempts=2)
    assert provider.calls == 2


def test_flatten_resume_content_produces_readable_plain_text():
    content = ResumeContent.model_validate(MINIMAL_CONTENT)
    text = flatten_resume_content(content)
    assert "Jamie Rivera" in text
    assert "jamie@example.com" in text
    assert "EXPERIENCE" in text
    assert "Acme Corp" in text and "Jan 2022 - Present" in text
    assert "- Built ETL pipelines using Python." in text
    assert "- AWS Certified Developer" in text
    assert "Languages: Python, SQL" in text


def test_render_resume_pdf_produces_valid_single_page_pdf(tmp_path: Path):
    output = tmp_path / "resume.pdf"
    content = ResumeContent.model_validate(MINIMAL_CONTENT)

    render_resume_pdf(content, output)

    assert output.exists()
    assert output.read_bytes()[:5] == b"%PDF-"
    import pymupdf

    with pymupdf.open(str(output)) as doc:
        assert doc.page_count == 1


def test_render_resume_pdf_handles_minimal_content(tmp_path: Path):
    output = tmp_path / "empty.pdf"
    content = ResumeContent(full_name="No Sections Yet")
    render_resume_pdf(content, output)
    assert output.exists()
    assert output.read_bytes()[:5] == b"%PDF-"


def test_render_resume_pdf_shrinks_to_fit_long_content(tmp_path: Path):
    """A resume with far more content than the base template should still
    end up on one page via the scale-down retry loop."""
    long_bullets = [f"Did a notable thing number {i} with measurable, specific impact on the team." for i in range(55)]
    content = ResumeContent(
        full_name="Overflowing Candidate",
        email="overflow@example.com",
        summary="A very accomplished person with a lot to say about it, at length, for testing purposes.",
        sections=[
            ResumeSection(
                title="EXPERIENCE",
                entries=[
                    EntryBlock(
                        heading_left="Big Company", heading_right="2015 - Present",
                        subheading_left="Senior Everything", subheading_right="Remote",
                        bullets=long_bullets,
                    )
                ],
            ),
            ResumeSection(title="SKILLS", skill_lines=[SkillLine(label="Everything", items=", ".join(f"Skill{i}" for i in range(60)))]),
        ],
    )

    output = tmp_path / "overflow.pdf"
    render_resume_pdf(content, output)

    assert output.exists()
    import pymupdf

    with pymupdf.open(str(output)) as doc:
        # Best-effort: should fit one page even at the smallest scale tried.
        assert doc.page_count == 1
