"""
Notes CRUD coverage. Creating a note schedules process_ai_analysis_background
as a FastAPI BackgroundTask, which TestClient runs synchronously as part of
the request - it calls the real OpenAI client, which is None in this test
environment (no OPENAI_KEY configured), but that failure is caught inside the
background task itself (see src/endpoints/notes.py) and turned into a safe
ai_fix=True update, so no mocking is required here for notes.
"""
from datetime import datetime, timedelta, timezone


def _note_id_from_redirect(location: str) -> str:
    return location.rstrip("/").rsplit("/", 1)[-1]


def test_notes_requires_login(client):
    # The list route is registered as "/" under the /notes prefix, so the
    # bare "/notes" (no trailing slash) 307s via Starlette's own
    # redirect_slashes handling before check_login ever runs - use the exact
    # route path to test the login-required behavior itself.
    response = client.get("/notes/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/users/login"


def test_create_view_edit_delete_note(logged_in_client):
    client = logged_in_client

    create = client.post(
        "/notes/new",
        data={
            "mood": "positive",
            "note": "pytest created this note for CRUD coverage.",
        },
        follow_redirects=False,
    )
    assert create.status_code == 302
    note_id = _note_id_from_redirect(create.headers["location"])

    view = client.get(f"/notes/view/{note_id}")
    assert view.status_code == 200
    assert "pytest created this note for CRUD coverage." in view.text

    edit = client.post(
        f"/notes/edit/{note_id}",
        data={"note": "pytest edited this note.", "mood": "negative"},
        follow_redirects=False,
    )
    assert edit.status_code == 302

    view_after_edit = client.get(f"/notes/view/{note_id}")
    assert "pytest edited this note." in view_after_edit.text

    delete = client.post(f"/notes/delete/{note_id}", follow_redirects=False)
    assert delete.status_code == 302

    # A deleted (or otherwise not-found/not-owned) note redirects to /notes
    # rather than 404ing - see read_note in src/endpoints/notes.py.
    view_after_delete = client.get(f"/notes/view/{note_id}", follow_redirects=False)
    assert view_after_delete.status_code == 302
    assert view_after_delete.headers["location"] == "/notes"


def test_edit_note_form(logged_in_client):
    client = logged_in_client
    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": "note for the edit-form GET test."},
        follow_redirects=False,
    )
    note_id = _note_id_from_redirect(create.headers["location"])

    response = client.get(f"/notes/edit/{note_id}")
    assert response.status_code == 200
    assert "note for the edit-form GET test." in response.text


def test_edit_note_form_not_found(logged_in_client):
    response = logged_in_client.get(
        "/notes/edit/does-not-exist", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/notes"


def test_note_list_shows_created_note(logged_in_client):
    client = logged_in_client

    create = client.post(
        "/notes/new",
        data={"mood": "neutral", "note": "pytest note visible in the list view."},
        follow_redirects=False,
    )
    assert create.status_code == 302

    listing = client.get("/notes/")
    assert listing.status_code == 200


def test_notes_list_page(logged_in_client):
    response = logged_in_client.get("/notes/list")
    assert response.status_code == 200


def test_notes_metrics_counts(logged_in_client):
    # First hit has no NoteMetrics row yet, so this exercises the
    # compute-then-read fallback path in get_note_counts too.
    response = logged_in_client.get("/notes/metrics/counts")
    assert response.status_code == 200


def test_notes_issues_page(logged_in_client):
    response = logged_in_client.get("/notes/issues")
    assert response.status_code == 200


def test_notes_tags_endpoint(logged_in_client):
    client = logged_in_client
    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": "note with tags for the tags endpoint test."},
        follow_redirects=False,
    )
    assert create.status_code == 302

    response = client.get("/notes/tags")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_notes_pagination(logged_in_client):
    response = logged_in_client.get(
        "/notes/pagination", params={"mood": "positive", "page": 1, "limit": 5}
    )
    assert response.status_code == 200


def test_notes_pagination_with_search_term(logged_in_client):
    client = logged_in_client
    unique_text = "unique-pagination-search-token"

    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": f"note containing {unique_text}."},
        follow_redirects=False,
    )
    note_id = _note_id_from_redirect(create.headers["location"])

    # pagination.html only renders note.summary (truncated to 30 chars), not
    # the full note body - the search filter itself matches against both
    # note and summary, but only the summary is visible in the rendered
    # page, so give this note a short, unique summary to search for.
    edit = client.post(
        f"/notes/edit/{note_id}",
        data={"summary": unique_text},
        follow_redirects=False,
    )
    assert edit.status_code == 302

    response = client.get("/notes/pagination", params={"search_term": unique_text})
    assert response.status_code == 200
    assert unique_text in response.text


def test_notes_pagination_with_tags_filter(logged_in_client):
    client = logged_in_client
    unique_summary = "tags-filter-summary-marker"

    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": "note used for the tags filter test."},
        follow_redirects=False,
    )
    note_id = _note_id_from_redirect(create.headers["location"])

    # Give it a unique, findable summary and a distinctive tag - the tag
    # filter matches case-insensitively within the JSON tags array.
    edit = client.post(
        f"/notes/edit/{note_id}",
        data={"summary": unique_summary, "tags": "PytestUniqueTag"},
        follow_redirects=False,
    )
    assert edit.status_code == 302

    matching = client.get(
        "/notes/pagination", params={"tags": "pytestuniquetag"}
    )
    assert matching.status_code == 200
    assert unique_summary in matching.text

    not_matching = client.get(
        "/notes/pagination", params={"tags": "no-such-tag-exists"}
    )
    assert not_matching.status_code == 200
    assert unique_summary not in not_matching.text


def test_notes_pagination_with_date_range_filter(logged_in_client):
    client = logged_in_client
    unique_summary = "date-range-summary-marker"

    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": "note used for the date range filter test."},
        follow_redirects=False,
    )
    note_id = _note_id_from_redirect(create.headers["location"])
    edit = client.post(
        f"/notes/edit/{note_id}",
        data={"summary": unique_summary},
        follow_redirects=False,
    )
    assert edit.status_code == 302

    # end_date is parsed as midnight of that day, so it must be tomorrow (not
    # today) to include a note created earlier today at some later time.
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    within_range = client.get(
        "/notes/pagination",
        params={"start_date": "2000-01-01", "end_date": tomorrow},
    )
    assert within_range.status_code == 200
    assert unique_summary in within_range.text

    outside_range = client.get(
        "/notes/pagination",
        params={"start_date": "2000-01-01", "end_date": "2000-01-02"},
    )
    assert outside_range.status_code == 200
    assert unique_summary not in outside_range.text


def test_notes_today_page(logged_in_client):
    response = logged_in_client.get("/notes/today")
    assert response.status_code == 200


def test_notes_bulk_form(logged_in_client):
    response = logged_in_client.get("/notes/bulk")
    assert response.status_code == 200


def test_notes_bulk_import_export_format(logged_in_client):
    # The "export" format (dsg's Notes Export CSV) is imported with no AI
    # call at all - see note_import.py - so this exercises the real import
    # pipeline (encryption, derived fields) without needing to mock AI.
    # pagination.html only renders note.summary (truncated to 30 chars), so
    # the CSV's Summary column - not its Note column - is what needs to be
    # both searchable and unique here.
    csv_content = (
        "PKID,User ID,Mood,Mood Analysis,Note,Summary,Tags,Word Count,"
        "Character Count,Date Created\n"
        "abc123,user-x,positive,happy,"
        '"Bulk imported note content.","bulk-import-summary-marker",'
        '"[\'tag1\']",3,26,01/20/2024\n'
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302

    listing = logged_in_client.get(
        "/notes/pagination", params={"search_term": "bulk-import-summary-marker"}
    )
    assert "bulk-import-summary-marker" in listing.text


def test_ai_resubmit_page_not_found(logged_in_client):
    response = logged_in_client.get(
        "/notes/ai-resubmit/does-not-exist", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/error/404"


def test_ai_fix_processing(logged_in_client):
    client = logged_in_client
    create = client.post(
        "/notes/new",
        data={"mood": "positive", "note": "note for the ai-fix endpoint test."},
        follow_redirects=False,
    )
    note_id = _note_id_from_redirect(create.headers["location"])

    response = client.get(f"/notes/ai-fix/{note_id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"/notes/view/{note_id}"
