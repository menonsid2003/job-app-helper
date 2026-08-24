from app.config import settings


def load_base_resume_text() -> str:
    path = settings.base_resume_ats_path
    if not path.exists():
        raise FileNotFoundError(
            f"Base resume not found at {path}. Drop your ATS-plain-text resume there "
            "(see backend/resume/README.txt), or paste it in Settings → Base Resume."
        )
    return path.read_text(encoding="utf-8")


def load_base_resume_text_or_empty() -> str:
    """Same as load_base_resume_text, but for the Settings editor — an empty
    textarea (rather than a 404) is the right way to show "nothing set yet"."""
    path = settings.base_resume_ats_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_base_resume_text(text: str) -> None:
    path = settings.base_resume_ats_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_experience_bank_text_or_empty() -> str:
    """Optional pool of additional job history/projects that doesn't fit the
    base resume's one-page layout — tailoring draws from this (in addition
    to the base resume) when picking what's most relevant per job. Empty
    string (not an error) when unset, since this is opt-in."""
    path = settings.experience_bank_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_experience_bank_text(text: str) -> None:
    path = settings.experience_bank_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
