import logging
import re
from datetime import date

from app.config import settings
from app.models import Job
from app.schemas import CriteriaConfig

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_SPREADSHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")

# Recognized column headers, matched case/punctuation-insensitively (see
# _normalize_header) — anything else in the target sheet is simply left
# blank in the appended row rather than guessed at. Extend this dict (and
# _field_value below) to recognize more of your own sheet's columns.
_FIELD_HEADER_ALIASES: dict[str, set[str]] = {
    "company": {"company"},
    "role": {"role", "title", "job title", "position"},
    "location": {"location"},
    "location_type": {"location type", "work type", "worktype", "remote"},
    "salary": {"salary range", "salary", "compensation"},
    "application_date": {"application date", "date applied", "applied date", "date"},
    "application_type": {"application type", "applied via", "how applied"},
    "status": {"status", "application status"},
    "notes": {"notes", "note"},
}
_ALIAS_TO_FIELD: dict[str, str] = {
    alias: field for field, aliases in _FIELD_HEADER_ALIASES.items() for alias in aliases
}


def push_applied_job(job: Job, criteria: CriteriaConfig, method_label: str) -> None:
    """Append one row to the configured Google Sheet for a job that just
    became "applied" — matched to the sheet's own header row (see
    _match_field), so column order/extra columns in the target sheet don't
    matter. Never raises: this is a best-effort side effect of a status
    change, not something that should ever fail the actual apply/update
    it's attached to. Failures are logged (same as connector failures
    elsewhere in this app) rather than surfaced anywhere in the UI."""
    config = criteria.google_sheets
    if not config.enabled:
        return

    spreadsheet_id = _parse_spreadsheet_id(config.spreadsheet_url)
    if not spreadsheet_id:
        logger.warning(
            "Google Sheets push skipped for job %s: google_sheets.enabled is on but "
            "spreadsheet_url isn't set (or isn't a valid Sheets URL/ID) in Settings.",
            job.id,
        )
        return
    if not settings.google_sheets_credentials_path.exists():
        logger.warning(
            "Google Sheets push skipped for job %s: no credentials file at %s — "
            "see Settings > Google Sheets Tracking for setup.",
            job.id, settings.google_sheets_credentials_path,
        )
        return

    try:
        service = _build_service()
        headers = _read_header_row(service, spreadsheet_id, config.sheet_name)
        row = _build_row(headers, job, method_label)
        _append_row(service, spreadsheet_id, config.sheet_name, row)
        logger.info("Pushed job %s to Google Sheets.", job.id)
    except Exception:
        logger.exception("Google Sheets push failed for job %s", job.id)


def _build_service():
    # Deferred import — google-api-python-client is a real dependency
    # weight to pay at process startup for a feature most installs of this
    # app won't turn on.
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_service_account_file(
        str(settings.google_sheets_credentials_path), scopes=_SCOPES
    )
    return build("sheets", "v4", credentials=credentials)


def _range(sheet_name: str, cells: str) -> str:
    # A bare range with no sheet-name prefix targets the spreadsheet's
    # first/default sheet, per the Sheets API — used when sheet_name is
    # left blank in Settings.
    return f"'{sheet_name}'!{cells}" if sheet_name else cells


def _read_header_row(service, spreadsheet_id: str, sheet_name: str) -> list[str]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=_range(sheet_name, "1:1"))
        .execute()
    )
    rows = result.get("values", [])
    return rows[0] if rows else []


def _append_row(service, spreadsheet_id: str, sheet_name: str, row: list[str]) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_range(sheet_name, "A1"),
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _parse_spreadsheet_id(url_or_id: str) -> str | None:
    url_or_id = (url_or_id or "").strip()
    if not url_or_id:
        return None
    match = _SPREADSHEET_ID_PATTERN.search(url_or_id)
    if match:
        return match.group(1)
    if "/" not in url_or_id and " " not in url_or_id:
        return url_or_id  # already a bare ID
    return None


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", header.strip().lower()).strip()


def _match_field(header: str) -> str | None:
    return _ALIAS_TO_FIELD.get(_normalize_header(header))


def _field_value(field: str, job: Job, method_label: str) -> str:
    if field == "company":
        return job.company
    if field == "role":
        return job.title
    if field == "location":
        return job.location
    if field == "location_type":
        if job.is_remote is True:
            return "Remote"
        if job.is_remote is False:
            return "In Person"
        return ""
    if field == "salary":
        return job.salary_text or ""
    if field == "application_date":
        today = date.today()
        return f"{today.month}/{today.day}/{today.year}"
    if field == "application_type":
        return method_label
    if field == "status":
        return "Applied"
    if field == "notes":
        return job.notes or ""
    return ""


def _build_row(headers: list[str], job: Job, method_label: str) -> list[str]:
    values = []
    for header in headers:
        field = _match_field(header)
        values.append(_field_value(field, job, method_label) if field else "")
    while values and values[-1] == "":
        values.pop()
    return values
