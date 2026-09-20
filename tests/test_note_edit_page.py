# -*- coding: utf-8 -*-
"""
The edit page puts the note body inside a <textarea> (Trumbowyg turns it into
the rich-text editor). Inside a textarea the browser decodes entities, so the
body must be HTML-escaped there - not marked `|safe`, which let a body
containing "</textarea><script>..." break out of the element and run.
"""

import pytest

from tests.test_note_reanalysis import _EditFormFields

BREAKOUT = "</textarea><script>alert('xss')</script>"
RICH_TEXT = "<p>Fish &amp; chips, <strong>and</strong> a &lt;tag&gt;</p>"


def _create(client, body):
    response = client.post(
        "/notes/new", data={"mood": "positive", "note": body}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _edit_page_fields(client, note_id):
    page = client.get(f"/notes/edit/{note_id}")
    assert page.status_code == 200
    parser = _EditFormFields()
    parser.feed(page.text)
    return page.text, parser.fields


def test_body_cannot_break_out_of_the_edit_textarea(logged_in_client):
    note_id = _create(logged_in_client, f"before {BREAKOUT} after")

    html, _ = _edit_page_fields(logged_in_client, note_id)

    assert "<script>alert('xss')</script>" not in html


@pytest.mark.parametrize(
    "body",
    [RICH_TEXT, f"before {BREAKOUT} after", "plain text, no markup"],
    ids=["rich-text", "hostile", "plain"],
)
def test_edit_textarea_round_trips_the_stored_body(logged_in_client, body):
    # Escaping must not corrupt notes: what the editor loads is exactly what
    # was stored, entities and all.
    note_id = _create(logged_in_client, body)

    _, fields = _edit_page_fields(logged_in_client, note_id)

    assert fields["note"] == body
