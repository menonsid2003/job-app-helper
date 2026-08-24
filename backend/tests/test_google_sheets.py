from datetime import date

import pytest

from app.google_sheets import _build_row, _match_field, _parse_spreadsheet_id, push_applied_job
from app.models import Job, JobStatus
from app.schemas import CriteriaConfig, GoogleSheetsCriteria

# The exact header row from the user's real example sheet.
REAL_HEADERS = [
    "Company", "Role", "Location", "Location Type", "Salary Range", "Application date",
    "Application Type", "Recruiter Contacted?", "Status", "Notes", "Interview?",
    "Interview Type", "Second Interview?", "Interview Type", "Offer?",
]


class FakeExecute:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeValues:
    def __init__(self, header_row):
        self.header_row = header_row
        self.appended: list[list[str]] = []
        self.append_calls: list[dict] = []

    def get(self, spreadsheetId, range):
        return FakeExecute({"values": [self.header_row]} if self.header_row else {})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.append_calls.append(
            {"spreadsheetId": spreadsheetId, "range": range, "valueInputOption": valueInputOption,
             "insertDataOption": insertDataOption}
        )
        self.appended.append(body["values"][0])
        return FakeExecute({})


class FakeSpreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class FakeService:
    def __init__(self, header_row):
        self.values = FakeValues(header_row)

    def spreadsheets(self):
        return FakeSpreadsheets(self.values)


def _job(**overrides) -> Job:
    defaults = dict(
        source="greenhouse", source_url="https://example.com/1", title="Data Engineer",
        company="Acme", location="Austin, TX", is_remote=False, salary_text="$100k-$130k",
        description="x", notes="Referred by a friend", status=JobStatus.APPLIED,
    )
    defaults.update(overrides)
    return Job(**defaults)


def _criteria(**overrides) -> CriteriaConfig:
    gs = GoogleSheetsCriteria(
        enabled=True, spreadsheet_url="https://docs.google.com/spreadsheets/d/abc123/edit", **overrides
    )
    return CriteriaConfig(google_sheets=gs)


# ---- _parse_spreadsheet_id ----


def test_parse_spreadsheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/1CBUiNzXkBgcwOBQeS_RLKsmjhRaOWH47rfHN554o96k/edit?usp=sharing"
    assert _parse_spreadsheet_id(url) == "1CBUiNzXkBgcwOBQeS_RLKsmjhRaOWH47rfHN554o96k"


def test_parse_spreadsheet_id_from_bare_id():
    assert _parse_spreadsheet_id("1CBUiNzXkBgcwOBQeS_RLKsmjhRaOWH47rfHN554o96k") == "1CBUiNzXkBgcwOBQeS_RLKsmjhRaOWH47rfHN554o96k"


def test_parse_spreadsheet_id_returns_none_for_blank():
    assert _parse_spreadsheet_id("") is None
    assert _parse_spreadsheet_id("   ") is None


# ---- header matching ----


def test_match_field_recognizes_known_header_variants_case_and_punctuation_insensitive():
    assert _match_field("Company") == "company"
    assert _match_field("company") == "company"
    assert _match_field("Location Type") == "location_type"
    assert _match_field("Application date") == "application_date"
    assert _match_field("Recruiter Contacted?") is None  # not a field we have data for


def test_build_row_maps_known_fields_from_the_real_sheets_headers():
    job = _job()
    row = _build_row(REAL_HEADERS, job, method_label="Manual")

    # Company, Role, Location, Location Type, Salary Range, Application date, Application Type,
    # Recruiter Contacted?(blank), Status, Notes -- then everything after Notes is blank and trimmed.
    assert row[0] == "Acme"
    assert row[1] == "Data Engineer"
    assert row[2] == "Austin, TX"
    assert row[3] == "In Person"
    assert row[4] == "$100k-$130k"
    assert row[5] == f"{date.today().month}/{date.today().day}/{date.today().year}"
    assert row[6] == "Manual"
    assert row[7] == ""  # Recruiter Contacted? -- unrecognized, left blank
    assert row[8] == "Applied"
    assert row[9] == "Referred by a friend"
    # Interview?/Interview Type/Second Interview?/Interview Type/Offer? are all
    # unrecognized and trailing -- trimmed off entirely rather than padded with blanks.
    assert len(row) == 10


def test_build_row_trims_trailing_blanks_but_keeps_internal_gaps():
    headers = ["Company", "Recruiter Contacted?", "Status"]
    row = _build_row(headers, _job(), method_label="Manual")
    assert row == ["Acme", "", "Applied"]  # internal gap preserved, no trailing trim needed here


def test_field_value_location_type_reflects_is_remote_tri_state():
    assert _build_row(["Location Type"], _job(is_remote=True), "Manual") == ["Remote"]
    assert _build_row(["Location Type"], _job(is_remote=False), "Manual") == ["In Person"]
    assert _build_row(["Location Type"], _job(is_remote=None), "Manual") == []  # blank -> trimmed


# ---- push_applied_job: gating ----


def test_push_applied_job_noop_when_disabled(monkeypatch):
    called = []
    monkeypatch.setattr("app.google_sheets._build_service", lambda: called.append(True))
    push_applied_job(_job(), CriteriaConfig(google_sheets=GoogleSheetsCriteria(enabled=False)), "Manual")
    assert called == []


def test_push_applied_job_noop_when_spreadsheet_url_blank(monkeypatch):
    called = []
    monkeypatch.setattr("app.google_sheets._build_service", lambda: called.append(True))
    push_applied_job(_job(), CriteriaConfig(google_sheets=GoogleSheetsCriteria(enabled=True, spreadsheet_url="")), "Manual")
    assert called == []


def test_push_applied_job_noop_when_credentials_file_missing(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr("app.google_sheets._build_service", lambda: called.append(True))
    monkeypatch.setattr("app.google_sheets.settings.google_sheets_credentials_path", tmp_path / "does-not-exist.json")
    push_applied_job(_job(), _criteria(), "Manual")
    assert called == []


# ---- push_applied_job: end to end against a fake Sheets service ----


def test_push_applied_job_appends_a_row_matching_real_headers(monkeypatch, tmp_path):
    creds = tmp_path / "service_account.json"
    creds.write_text("{}")
    monkeypatch.setattr("app.google_sheets.settings.google_sheets_credentials_path", creds)

    fake_service = FakeService(REAL_HEADERS)
    monkeypatch.setattr("app.google_sheets._build_service", lambda: fake_service)

    push_applied_job(_job(), _criteria(), "Auto-apply")

    assert len(fake_service.values.appended) == 1
    row = fake_service.values.appended[0]
    assert row[0] == "Acme"
    assert row[6] == "Auto-apply"
    assert fake_service.values.append_calls[0]["valueInputOption"] == "USER_ENTERED"


def test_push_applied_job_uses_sheet_name_in_range_when_set(monkeypatch, tmp_path):
    creds = tmp_path / "service_account.json"
    creds.write_text("{}")
    monkeypatch.setattr("app.google_sheets.settings.google_sheets_credentials_path", creds)

    fake_service = FakeService(["Company", "Status"])
    monkeypatch.setattr("app.google_sheets._build_service", lambda: fake_service)

    push_applied_job(_job(), _criteria(sheet_name="Content_tracker1"), "Manual")

    assert fake_service.values.append_calls[0]["range"] == "'Content_tracker1'!A1"


def test_push_applied_job_never_raises_when_the_api_call_fails(monkeypatch, tmp_path):
    creds = tmp_path / "service_account.json"
    creds.write_text("{}")
    monkeypatch.setattr("app.google_sheets.settings.google_sheets_credentials_path", creds)

    def _explode():
        raise RuntimeError("network is on fire")

    monkeypatch.setattr("app.google_sheets._build_service", _explode)

    push_applied_job(_job(), _criteria(), "Manual")  # must not raise
