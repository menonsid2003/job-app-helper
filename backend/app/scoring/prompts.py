from datetime import date

from app.schemas import CriteriaConfig, JobListing

SYSTEM_PROMPT = """You are an assistant that screens job postings for a candidate. \
You read a candidate's resume, their target criteria, and a job description, then \
return a single JSON object scoring the fit. Be precise and conservative — do not \
invent requirements the posting does not state. Job descriptions often contain long \
shared boilerplate (compensation philosophy, sales-incentive language, DEI \
statements, benefits, "why work here" blurbs) that is identical across many \
unrelated postings at the same company — ignore that boilerplate when scoring and \
base the score only on the actual role title and job-specific responsibilities/ \
requirements. If the role's actual function (e.g. HR, Sales, Legal, Marketing, \
Finance) is unrelated to the candidate's resume and target roles, score it low \
(below 20) even if the boilerplate mentions the candidate's tech stack. Respond \
with JSON only, no prose outside the JSON object."""

JSON_SHAPE_TEMPLATE = """{
  "score": <integer 0-100>,
  "reasoning": "<short explanation of the score>",
  "matched_keywords": ["..."],
  "missing_requirements": ["..."],
  "role_category": "<exactly one of: __ROLE_CATEGORIES__>",
  "red_flags": ["..."],
  "work_authorization": {
    "citizenship_required": true/false,
    "security_clearance_required": true/false,
    "sponsorship_mentioned": "yes" | "no" | "not_mentioned",
    "hard_exclude": true/false
  },
  "location_us_eligible": true/false,
  "is_remote": true/false/null
}"""


def _build_json_shape(role_categories: list[str]) -> str:
    all_categories = list(dict.fromkeys([*role_categories, "Other"]))  # dedupe, keep order, "Other" always last resort
    return JSON_SHAPE_TEMPLATE.replace("__ROLE_CATEGORIES__", ", ".join(all_categories))


def build_scoring_prompt(
    resume_text: str, criteria: CriteriaConfig, job: JobListing, today: date | None = None
) -> tuple[str, str]:
    weights = criteria.scoring_weights
    today = today or date.today()
    json_shape = _build_json_shape(criteria.role_categories)
    user_prompt = f"""## Today's date
{today.isoformat()}
When judging seniority_fit or reasoning about how much experience a role/tenure represents \
(e.g. a resume line like "Aug 2025 - Present"), compute elapsed time against this date, not \
whatever date you'd otherwise assume — do not rely on your own sense of the current date.

## Candidate resume (ATS plain text)
{resume_text}

## Candidate target criteria
Target roles: {", ".join(criteria.target_roles) or "any"}
Preferred locations: {", ".join(criteria.locations) or "any"}
Seniority: {", ".join(criteria.seniority) or "any"}
Salary floor: {criteria.salary_min if criteria.salary_min else "not specified"}
Must-have keywords: {", ".join(criteria.must_have_keywords) or "none"}
Nice-to-have keywords: {", ".join(criteria.nice_to_have_keywords) or "none"}
Employment type: {"full-time preferred; contract is acceptable but should generally score a little lower than an equivalent full-time role, all else equal" if criteria.prefer_full_time else "no preference between full-time and contract"}

## Scoring rubric weights (apply these when computing the single 0-100 score)
title_match: {weights.title_match}
skills_overlap: {weights.skills_overlap}
location_fit: {weights.location_fit}
seniority_fit: {weights.seniority_fit}
salary_fit: {weights.salary_fit}

## Job posting
Title: {job.title}
Company: {job.company}
Location: {job.location}
Salary: {job.salary_text or "not disclosed"}

Description:
{job.description}

## Work authorization instructions
Read the full description carefully, including any EEO/compliance boilerplate, for \
language about citizenship requirements, security clearance requirements, or \
sponsorship. Phrasing varies a lot (e.g. "must be a U.S. person", "active TS/SCI \
required", "unable to sponsor at this time"). Set citizenship_required and \
security_clearance_required to true only if the posting explicitly requires them. \
Set sponsorship_mentioned to "no" only if the posting explicitly states it will not \
sponsor; set it to "yes" if it explicitly says sponsorship is available; otherwise \
"not_mentioned". Do NOT treat silence about sponsorship as a reason to set hard_exclude. \
Set hard_exclude to true only if citizenship_required is true, OR \
security_clearance_required is true, OR sponsorship_mentioned is "no".

## Experience-gap leniency
When judging seniority_fit, and when deciding whether to list something under missing_requirements or \
red_flags, do not treat a small gap between the candidate's actual demonstrated experience (computed from \
the resume, per the date instructions above) and the posting's stated required years as a meaningful \
mismatch. Specifically: if the posting asks for at most about 1 year more experience than the candidate \
actually has (e.g. posting wants 2-3 years, candidate has 1-2), treat that as a practical match — score \
seniority_fit accordingly and don't cite it as a red flag or missing requirement. Only treat it as a real \
gap once the difference is larger than that (e.g. posting wants 5+ years, candidate has 1-2).

## Location eligibility instructions (US-only is a hard requirement)
The listed location field is "{job.location}", which may be ambiguous (e.g. a \
bare "Remote" with no country stated). Read the full description for any \
explicit statement of which countries/states/regions are eligible. Set \
location_us_eligible to false ONLY if the description explicitly restricts \
this role to specific non-US countries or regions with no US option (e.g. \
"this role is only available in the UK and Ireland", "EMEA candidates only"). \
If the description mentions the US as one of multiple eligible options, or \
doesn't clarify further, or says nothing beyond "Remote", set \
location_us_eligible to true — do not guess exclusion from an ambiguous \
location field alone.

## Remote-work instructions
The listed location field is "{job.location}", which often does not reflect the role's actual \
remote-work policy — a source platform sometimes lists an anchor city even for a fully-remote \
role, or leaves location blank even though the description states a clear policy. Read the full \
description for the role's own stated remote-work policy and set is_remote accordingly: true if \
the description says the role is remote, fully remote, work-from-home, or remote-first with no \
required office days; false if it explicitly requires being on-site, in-office, or hybrid with \
required in-office days; null if the description says nothing about remote-work policy either \
way. Do not infer remote status from "remote"/"on-site"/"in-person" used in an unrelated sense — \
e.g. a payments company describing "in-person payments" as a product feature, "on-site SEO" \
referring to a website, or "in-person meetings" describing a communication style rather than a \
location requirement. Only set is_remote from language that actually describes where the person \
in this specific role works.

## Output
Respond with exactly one JSON object matching this shape:
{json_shape}
"""
    return SYSTEM_PROMPT, user_prompt
