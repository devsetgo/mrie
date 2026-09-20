# -*- coding: utf-8 -*-
"""
Editing a note re-runs the AI analysis - but only when the body changed and
the user did not hand-correct an AI-owned field (summary / tags /
mood_analysis) in the same submit; see update_note in src/endpoints/notes.py.

The forms here are read back from the rendered edit page and submitted the way
a browser would (enabled named fields only), not hand-built: the page submits
`summary` and `tags` on every save even when untouched (they are `readonly`,
which - unlike `disabled` - still submits), so "untouched" has to compare
equal to what the page rendered.
"""

from html.parser import HTMLParser

import pytest
from loguru import logger

from src.endpoints import notes as notes_endpoint


class _EditFormFields(HTMLParser):
    """Collects the fields a browser would submit from the edit page."""

    NAMES = {"note", "summary", "tags", "mood", "mood_analysis"}

    def __init__(self):
        super().__init__()
        self.fields = {}
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "textarea" and a.get("name") in self.NAMES:
            self._textarea = a["name"]
            self.fields[self._textarea] = ""
        elif tag == "input" and a.get("name") in self.NAMES and "disabled" not in a:
            if a.get("type") == "radio" and "checked" not in a:
                return
            self.fields[a["name"]] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.fields[self._textarea] += data


@pytest.fixture
def reanalysis_calls(monkeypatch):
    calls = []

    async def fake_background(note_id, content, user_id, mood_process=None):
        calls.append({"note_id": note_id, "content": content, "user_id": user_id})

    monkeypatch.setattr(
        notes_endpoint, "process_ai_analysis_background", fake_background
    )
    return calls


@pytest.fixture
def note_id(logged_in_client, reanalysis_calls):
    create = logged_in_client.post(
        "/notes/new",
        data={"mood": "positive", "note": "Original body of the note."},
        follow_redirects=False,
    )
    assert create.status_code == 303
    reanalysis_calls.clear()  # creating a note queues its own analysis
    return create.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _submit_edit(client, note_id, **overrides):
    page = client.get(f"/notes/edit/{note_id}")
    assert page.status_code == 200
    parser = _EditFormFields()
    parser.feed(page.text)
    form = {**parser.fields, **overrides}
    response = client.post(f"/notes/edit/{note_id}", data=form, follow_redirects=False)
    assert response.status_code == 303
    return form


def test_editing_the_body_queues_reanalysis(
    logged_in_client, note_id, reanalysis_calls
):
    _submit_edit(logged_in_client, note_id, note="A brand new body.")

    assert len(reanalysis_calls) == 1
    assert reanalysis_calls[0]["note_id"] == note_id
    assert reanalysis_calls[0]["content"] == "A brand new body."


def test_resubmitting_an_unchanged_note_does_not_reanalyze(
    logged_in_client, note_id, reanalysis_calls
):
    _submit_edit(logged_in_client, note_id)

    assert reanalysis_calls == []


@pytest.mark.parametrize(
    "hand_edited",
    [
        {"summary": "My own summary"},
        {"tags": "mine, corrected"},
        {"mood_analysis": "happy"},
    ],
    ids=["summary", "tags", "mood_analysis"],
)
def test_hand_edited_ai_field_suppresses_reanalysis(
    logged_in_client, note_id, reanalysis_calls, hand_edited
):
    # Re-analyzing would immediately overwrite the correction just made.
    _submit_edit(logged_in_client, note_id, note="Changed body.", **hand_edited)

    assert reanalysis_calls == []


def test_changing_only_the_mood_does_not_reanalyze(
    logged_in_client, note_id, reanalysis_calls
):
    _submit_edit(logged_in_client, note_id, mood="negative")

    assert reanalysis_calls == []


# ---- when the analysis itself fails ----


def _good_analysis():
    return {
        "tags": {"tags": ["reflection"]},
        "summary": "A Quiet Day",
        "mood_analysis": "happy",
        "mood": {"mood": "positive"},
    }


def test_failed_reanalysis_flags_the_note_notifies_and_logs_the_traceback(
    logged_in_client, monkeypatch
):
    notified = []

    async def record_notification(**kwargs):
        notified.append(kwargs)

    monkeypatch.setattr(notes_endpoint, "create_notification", record_notification)

    # 1. A successful analysis on create leaves the note healthy (ai_fix False).
    async def succeeds(content, mood_process=None):
        return _good_analysis()

    monkeypatch.setattr(notes_endpoint.ai, "get_analysis", succeeds)
    create = logged_in_client.post(
        "/notes/new",
        data={"mood": "positive", "note": "First version of the note."},
        follow_redirects=False,
    )
    note_id = create.headers["location"].rstrip("/").rsplit("/", 1)[-1]
    assert note_id not in logged_in_client.get("/notes/issues").text
    notified.clear()

    # 2. Editing the body re-analyzes; this time the model call blows up.
    async def fails(content, mood_process=None):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(notes_endpoint.ai, "get_analysis", fails)
    lines = []
    sink_id = logger.add(lambda message: lines.append(str(message)), level="ERROR")
    try:
        _submit_edit(logged_in_client, note_id, note="Second version of the note.")
    finally:
        logger.remove(sink_id)

    # The failure is visible: note lands on the AI Issues page, the user is
    # told, and the log carries the cause and traceback rather than just str(e).
    assert note_id in logged_in_client.get("/notes/issues").text
    assert [n["category"] for n in notified] == ["error"]
    assert notified[0]["note_id"] == note_id
    assert any("Traceback" in line and "model unavailable" in line for line in lines)
