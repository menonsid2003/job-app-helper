import logging

import pymupdf
from pydantic import BaseModel, Field, ValidationError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ---- Structured resume content ----
# The tailoring LLM call parses the base resume AND tailors it for the job
# in one shot, returning this structure rather than freeform text — the PDF
# renderer below needs the company/date and title/location split into
# separate fields to lay them out as a two-column header the way the base
# resume's own template does; that split can't be recovered reliably from
# plain text after the fact.


class EntryBlock(BaseModel):
    heading_left: str = ""  # company / institution / project name (bold)
    heading_right: str = ""  # date range, right-aligned on the same line (bold); blank = no second column
    subheading_left: str = ""  # title, or a single full-width line like "B.S., CS | GPA: 3.8" (italic)
    subheading_right: str = ""  # location, right-aligned (italic); blank = subheading is a single full-width line
    bullets: list[str] = Field(default_factory=list)


class SkillLine(BaseModel):
    label: str = ""  # e.g. "Languages" — rendered as "Languages: " in bold, inline with items
    items: str = ""


class ResumeSection(BaseModel):
    """Exactly one of entries / plain_bullets / skill_lines should be
    populated — which one determines how the section renders (job/education
    style entries, a flat bullet list like Certifications, or label: items
    lines like Skills)."""

    title: str
    entries: list[EntryBlock] = Field(default_factory=list)
    plain_bullets: list[str] = Field(default_factory=list)
    skill_lines: list[SkillLine] = Field(default_factory=list)


class ResumeContent(BaseModel):
    full_name: str
    phone: str = ""
    email: str = ""
    links: list[str] = Field(default_factory=list)
    summary: str = ""
    sections: list[ResumeSection] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a resume editor and formatter. You take a candidate's existing plain-text resume \
and a target job description, then produce a tailored version as STRUCTURED JSON that maps onto a fixed \
visual template (centered name/contact header, a summary paragraph, then sections — each with a bold, \
colored title and a rule line under it).

This is editing, not ghostwriting:
- Do not invent experience, employers, titles, dates, or skills the candidate doesn't already have — every \
fact must come from either the base resume or the experience bank below (when one is provided), never made up.
- Preserve the candidate's actual employment history, companies, titles, and dates exactly as given.
- You may: reorder bullets within an entry to put the most relevant ones first, re-word bullets to use \
terminology/keywords from the job description IF the underlying fact is already true, trim less-relevant \
bullets, and adjust the summary to emphasize fit for this role.
- If a FULL EXPERIENCE BANK is provided below the base resume, treat it as an additional pool of real \
projects/roles/bullets the candidate has but that don't fit the base resume's one-page layout. You may swap \
a bank entry or bullet IN PLACE OF a less-relevant base-resume one when it's a better fit for this specific \
job — e.g. an older role or side project that's more relevant here than what the base resume shows. Swap, \
don't just append — the result must still fit one page.
- If a PREVIOUS TAILORED VERSION and USER CORRECTION are provided below, this is a targeted revision, not a \
fresh tailoring pass: start from that previous version and apply the correction exactly, changing as little \
else as possible. Don't re-derive the whole resume from scratch and don't reintroduce whatever the \
correction asked you to remove/change.
- Preserve the original resume's section order and section titles (e.g. if the original says "EXPERIENCE", \
don't rename it "Work History").
- Keep total content close to the original's length — it must still fit comfortably on ONE page.

Map the resume into this exact JSON shape:
{
  "full_name": "...", "phone": "...", "email": "...",
  "links": ["linkedin.com/in/...", "github.com/...", ...],
  "summary": "one paragraph, no line breaks",
  "sections": [
    {
      "title": "SECTION HEADING, MATCH THE ORIGINAL'S CASE",
      "entries": [
        {
          "heading_left": "Company, institution, or project name",
          "heading_right": "Date range — blank string if there isn't one",
          "subheading_left": "Job title, OR a single line like 'B.S., Computer Science | GPA: 3.8', OR blank",
          "subheading_right": "Location — blank if there isn't one, or if subheading_left is meant as one full-width line",
          "bullets": ["bullet one", "bullet two"]
        }
      ],
      "plain_bullets": [],
      "skill_lines": []
    }
  ]
}

Each section uses EXACTLY ONE of "entries" (job/project/education-style entries), "plain_bullets" (a flat \
bullet list with no heading, e.g. Certifications), or "skill_lines" (label/items pairs, e.g. Skills). Leave \
the other two as empty lists for that section.

Worked example (structure only — this is a different, made-up candidate; never reuse these facts):
{
  "full_name": "Jordan Lee", "phone": "+1 555-0100", "email": "jordan@example.com",
  "links": ["linkedin.com/in/jordanlee"],
  "summary": "Backend engineer with 5 years building distributed systems, focused on reliability and API design.",
  "sections": [
    {"title": "EXPERIENCE", "entries": [
      {"heading_left": "Acme Corp", "heading_right": "Jan 2022 - Present",
       "subheading_left": "Senior Engineer", "subheading_right": "Remote",
       "bullets": ["Led migration of the billing service to event-driven architecture, cutting p99 latency 40%.",
                    "Mentored 2 junior engineers through their first on-call rotations."]}
    ], "plain_bullets": [], "skill_lines": []},
    {"title": "CERTIFICATIONS", "entries": [], "plain_bullets": ["AWS Certified Developer - Associate"], "skill_lines": []},
    {"title": "SKILLS", "entries": [], "plain_bullets": [], "skill_lines": [
      {"label": "Languages", "items": "Python, Go, SQL"},
      {"label": "Infrastructure", "items": "AWS, Docker, Terraform"}
    ]},
    {"title": "EDUCATION", "entries": [
      {"heading_left": "State University", "heading_right": "2016 - 2020",
       "subheading_left": "B.S., Computer Science | GPA: 3.8", "subheading_right": "", "bullets": []}
    ], "plain_bullets": [], "skill_lines": []}
  ]
}

Respond with JSON only, no prose outside the JSON object."""


def build_tailoring_prompt(
    base_resume_text: str, job_title: str, job_company: str, job_description: str,
    experience_bank_text: str = "", previous_tailored_text: str = "", correction: str = "",
) -> tuple[str, str]:
    bank_section = (
        f"\n\n## Full experience bank (additional job history/projects not in the base resume — pull from "
        f"this too when it's a better fit for this job; same no-invention rule, must still fit one page)\n"
        f"{experience_bank_text}"
        if experience_bank_text.strip()
        else ""
    )
    correction_section = (
        f"\n\n## Previous tailored version (revise this — don't start over from scratch)\n"
        f"{previous_tailored_text}\n\n"
        f"## User correction — apply this exactly, keep everything else from the previous version as-is\n"
        f"{correction}"
        if correction.strip()
        else ""
    )
    user_prompt = f"""## Candidate's base resume (ATS plain text)
{base_resume_text}{bank_section}{correction_section}

## Target job
Title: {job_title}
Company: {job_company}

Description:
{job_description}

## Output
Respond with exactly one JSON object matching the shape and worked example in the system prompt."""
    return SYSTEM_PROMPT, user_prompt


def tailor_resume_content(
    provider: LLMProvider, base_resume_text: str, job_title: str, job_company: str, job_description: str,
    experience_bank_text: str = "", previous_tailored_text: str = "", correction: str = "",
    max_attempts: int = 3,
) -> ResumeContent:
    system, user = build_tailoring_prompt(
        base_resume_text, job_title, job_company, job_description,
        experience_bank_text, previous_tailored_text, correction,
    )

    last_error: ValidationError | None = None
    for attempt in range(1, max_attempts + 1):
        raw = provider.complete_json(system, user)
        try:
            return ResumeContent.model_validate(raw)
        except ValidationError as exc:
            last_error = exc
            logger.warning("Tailored resume JSON failed validation (attempt %d/%d): %s", attempt, max_attempts, exc)

    logger.error("Resume tailoring output failed validation after %d attempts", max_attempts)
    raise last_error


def flatten_resume_content(content: ResumeContent) -> str:
    """Deterministic plain-text rendering of the structured content — used
    for the ATS text download, the base-vs-tailored diff view, and as the
    resume text handed to the agent-apply prompt. Not another LLM call."""
    lines = [content.full_name]
    contact_bits = [b for b in (content.phone, content.email, *content.links) if b]
    if contact_bits:
        lines.append(" | ".join(contact_bits))
    if content.summary:
        lines.append("")
        lines.append(content.summary)

    for section in content.sections:
        lines.append("")
        lines.append(section.title)
        for entry in section.entries:
            header = entry.heading_left
            if entry.heading_right:
                header = f"{header}    {entry.heading_right}"
            lines.append(header)
            if entry.subheading_left or entry.subheading_right:
                sub = entry.subheading_left
                if entry.subheading_right:
                    sub = f"{sub}    {entry.subheading_right}"
                lines.append(sub)
            lines.extend(f"- {bullet}" for bullet in entry.bullets)
        lines.extend(f"- {bullet}" for bullet in section.plain_bullets)
        for skill in section.skill_lines:
            lines.append(f"{skill.label}: {skill.items}" if skill.label else skill.items)

    return "\n".join(lines)


# ---- PDF rendering ----
# Reproduces the base_resume.pdf template's exact fonts/colors/layout,
# measured directly from that file (LiberationSerif — metrically compatible
# with reportlab's built-in Times family — 16pt navy centered name, 10pt
# navy section headers each followed by a 0.75pt gray rule, bold company/
# dates + italic title/location two-column entry headers, 9.5pt body).

_NAVY = colors.Color(0x1F / 255, 0x3B / 255, 0x57 / 255)
_RULE_GRAY = colors.Color(0.267, 0.267, 0.267)
_FONT_REGULAR = "Times-Roman"
_FONT_BOLD = "Times-Bold"
_FONT_ITALIC = "Times-Italic"

_MARGIN = 35 / 72 * inch  # matches the measured 35pt left/right margins
_CONTENT_WIDTH = LETTER[0] - 2 * _MARGIN

# Scale factors tried in order until the rendered PDF fits on one page.
_FIT_SCALES = (1.0, 0.94, 0.88, 0.82, 0.76)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _two_col(left_text: str, right_text: str, font: str, size: float, leading: float, color=colors.black):
    style_l = ParagraphStyle("l", fontName=font, fontSize=size, leading=leading, textColor=color)
    if not right_text:
        return Paragraph(_esc(left_text), style_l)

    style_r = ParagraphStyle("r", fontName=font, fontSize=size, leading=leading, textColor=color, alignment=TA_RIGHT)
    table = Table(
        [[Paragraph(_esc(left_text), style_l), Paragraph(_esc(right_text), style_r)]],
        colWidths=[_CONTENT_WIDTH * 0.62, _CONTENT_WIDTH * 0.38],
    )
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _build_story(content: ResumeContent, scale: float) -> list:
    def sz(base: float) -> float:
        return base * scale

    def leading(size: float) -> float:
        return size * 1.18

    name_style = ParagraphStyle(
        "name", fontName=_FONT_BOLD, fontSize=sz(16), leading=leading(sz(16)),
        textColor=_NAVY, alignment=TA_CENTER, spaceAfter=sz(3),
    )
    contact_style = ParagraphStyle(
        "contact", fontName=_FONT_REGULAR, fontSize=sz(9), leading=leading(sz(9)),
        alignment=TA_CENTER, spaceAfter=sz(9),
    )
    summary_style = ParagraphStyle(
        "summary", fontName=_FONT_REGULAR, fontSize=sz(9.5), leading=leading(sz(9.5)),
        alignment=TA_JUSTIFY, spaceAfter=sz(9),
    )
    section_header_style = ParagraphStyle(
        "section_header", fontName=_FONT_BOLD, fontSize=sz(10), leading=leading(sz(10)),
        textColor=_NAVY, spaceBefore=sz(9), spaceAfter=sz(1),
    )
    bullet_style = ParagraphStyle(
        "bullet", fontName=_FONT_REGULAR, fontSize=sz(9.5), leading=leading(sz(9.5)),
        leftIndent=sz(15.8), bulletIndent=sz(5), spaceAfter=sz(1),
    )
    skill_style = ParagraphStyle(
        "skill", fontName=_FONT_REGULAR, fontSize=sz(9.5), leading=leading(sz(9.5)), spaceAfter=sz(3),
    )

    story: list = [Paragraph(_esc(content.full_name), name_style)]

    contact_bits = [b for b in (content.phone, content.email, *content.links) if b]
    if contact_bits:
        story.append(Paragraph(_esc("  |  ".join(contact_bits)), contact_style))

    if content.summary:
        story.append(Paragraph(_esc(content.summary), summary_style))

    for section in content.sections:
        story.append(Paragraph(_esc(section.title), section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.75, color=_RULE_GRAY, spaceBefore=0, spaceAfter=sz(4)))

        for entry in section.entries:
            story.append(_two_col(entry.heading_left, entry.heading_right, _FONT_BOLD, sz(9.5), leading(sz(9.5))))
            if entry.subheading_left or entry.subheading_right:
                story.append(_two_col(entry.subheading_left, entry.subheading_right, _FONT_ITALIC, sz(9.5), leading(sz(9.5))))
            for bullet in entry.bullets:
                story.append(Paragraph(_esc(bullet), bullet_style, bulletText="•"))
            story.append(Spacer(1, sz(3)))

        for bullet in section.plain_bullets:
            story.append(Paragraph(_esc(bullet), bullet_style, bulletText="•"))

        for skill in section.skill_lines:
            text = f"<b>{_esc(skill.label)}:</b> {_esc(skill.items)}" if skill.label else _esc(skill.items)
            story.append(Paragraph(text, skill_style))

    return story


def _render_at_scale(content: ResumeContent, output_path, scale: float) -> None:
    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        topMargin=0.35 * inch, bottomMargin=0.4 * inch,
        leftMargin=_MARGIN, rightMargin=_MARGIN,
    )
    doc.build(_build_story(content, scale))


def _pdf_page_count(path) -> int:
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


def render_resume_pdf(content: ResumeContent, output_path) -> None:
    """Render to output_path, shrinking font sizes/spacing in steps until
    the result fits on one page. Falls back to the smallest scale (still
    written to disk) if even that doesn't fit — never raises."""
    for i, scale in enumerate(_FIT_SCALES):
        _render_at_scale(content, output_path, scale)
        if _pdf_page_count(output_path) <= 1:
            return
        logger.info("Tailored resume PDF didn't fit on one page at scale %.2f, retrying smaller…", scale)

    logger.warning("Tailored resume PDF still exceeds one page at minimum scale (%.2f): %s", _FIT_SCALES[-1], output_path)
