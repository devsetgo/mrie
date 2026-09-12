"""
Notes CSV import coverage (src/functions/note_import.py). The "export"
format (dsg's Notes Export CSV, no AI call at all) is already covered via
/notes/bulk in test_notes.py; this covers the "simple" format's
AI-processing branch (process_ai/process_note - mocking ai.get_analysis,
since there's no real OpenAI key in this environment), plus the module's
pure helper functions directly (no DB, no HTTP, no mocking needed).
"""
import csv
import io

import pytest

from src.functions import note_import


# ---- pure helper functions ----


def test_detect_csv_format_simple():
    assert note_import.detect_csv_format(note_import.SIMPLE_HEADERS) == "simple"


def test_detect_csv_format_export():
    assert note_import.detect_csv_format(note_import.EXPORT_HEADERS) == "export"


def test_detect_csv_format_unknown():
    assert note_import.detect_csv_format(["foo", "bar"]) is None


def test_parse_tags_field_empty():
    assert note_import.parse_tags_field(None) == []
    assert note_import.parse_tags_field("") == []


def test_parse_tags_field_list_passthrough():
    assert note_import.parse_tags_field(["a", "b"]) == ["a", "b"]


def test_parse_tags_field_python_repr_string():
    # dsg's export template renders Tags as Python's default str(list) repr.
    assert note_import.parse_tags_field("['alpha', 'beta']") == ["alpha", "beta"]


def test_parse_tags_field_comma_separated_string():
    assert note_import.parse_tags_field("alpha, beta ,gamma") == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_parse_tags_field_malformed_repr_falls_back_to_comma_split():
    assert note_import.parse_tags_field("[unclosed, alpha") == ["[unclosed", "alpha"]


def test_parse_date_with_time():
    dt = note_import.parse_date("01/15/2024 14:30")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 1, 15, 14, 30)


def test_parse_date_without_time():
    dt = note_import.parse_date("01/15/2024")
    assert (dt.year, dt.month, dt.day) == (2024, 1, 15)


def test_parse_date_dateutil_fallback():
    dt = note_import.parse_date("2024-01-15")
    assert (dt.year, dt.month, dt.day) == (2024, 1, 15)


def test_parse_date_offset_aware_converted_to_naive_utc():
    dt = note_import.parse_date("2024-01-15T10:00:00+05:00")
    assert dt.tzinfo is None
    assert dt.hour == 5  # 10:00 at +05:00 is 05:00 UTC


def test_parse_date_unparseable_returns_none():
    assert note_import.parse_date("not a date") is None


def test_validate_csv_headers_success():
    reader = csv.DictReader(io.StringIO("my_note,mood,date_created\n"))
    assert note_import.validate_csv_headers(reader) == {"status": "success"}


def test_validate_csv_headers_failure():
    reader = csv.DictReader(io.StringIO("foo,bar\n"))
    result = note_import.validate_csv_headers(reader)
    assert result["status"]["error"] == "Invalid CSV file"
    assert "my_note" in result["status"]["missing_headers"]


# ---- read_notes_from_file via the /notes/bulk endpoint ----


@pytest.fixture
def mock_note_ai(monkeypatch):
    async def fake_get_analysis(content, mood_process=None):
        return {
            "tags": {"tags": ["imported", "pytest"]},
            "summary": "ai-import-marker-xyz",
            "mood_analysis": "content",
            "mood": {"mood": "positive"},
        }

    monkeypatch.setattr("src.functions.ai.get_analysis", fake_get_analysis)


@pytest.fixture
def mock_note_ai_failure(monkeypatch):
    async def fake_get_analysis(content, mood_process=None):
        raise RuntimeError("simulated AI failure")

    monkeypatch.setattr("src.functions.ai.get_analysis", fake_get_analysis)


def test_bulk_import_simple_format(logged_in_client, mock_note_ai):
    csv_content = (
        "my_note,mood,date_created\n"
        '"Simple format import test note.",positive,01/15/2024\n'
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302

    # process_ai/process_note run synchronously as part of the background
    # task under TestClient, so by the time the request returns the note
    # already has its real AI-generated summary (not the "processing"
    # placeholder) and ai_fix has cleared - it should no longer be listed
    # as needing a fix.
    listing = logged_in_client.get(
        "/notes/pagination", params={"search_term": "ai-import-marker-xyz"}
    )
    assert "ai-import-marker-xyz" in listing.text

    issues = logged_in_client.get("/notes/issues")
    assert "ai-import-marker-xyz" not in issues.text


def test_bulk_import_simple_format_defers_to_ai_mood_when_invalid(
    logged_in_client, mock_note_ai
):
    # A mood outside positive/negative/neutral gets queued as "processing"
    # at creation, then process_note() falls back to the AI-derived mood
    # (mocked to "positive" here) once analysis completes.
    csv_content = (
        "my_note,mood,date_created\n"
        '"Note with an invalid mood value.",curious,01/15/2024\n'
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302

    listing = logged_in_client.get(
        "/notes/pagination", params={"search_term": "ai-import-marker-xyz", "mood": "positive"}
    )
    assert "ai-import-marker-xyz" in listing.text


def test_bulk_import_simple_format_ai_failure_leaves_note_flagged(
    logged_in_client, mock_note_ai_failure
):
    # process_note()'s except block only logs and returns - it never
    # touches the DB - so a note whose AI processing fails keeps its
    # "processing" placeholder summary and stays flagged as needing a fix.
    csv_content = (
        "my_note,mood,date_created\n"
        '"Note that fails AI processing.",positive,01/15/2024\n'
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302

    issues = logged_in_client.get("/notes/issues")
    assert issues.status_code == 200
    assert "processing" in issues.text


def test_bulk_import_unrecognized_format_is_a_noop(logged_in_client):
    # detect_csv_format() returns None for headers matching neither
    # accepted format - read_notes_from_file logs the validation error and
    # returns early without creating anything or raising.
    csv_content = "foo,bar\nvalue1,value2\n"
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302
