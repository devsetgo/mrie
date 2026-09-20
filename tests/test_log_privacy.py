# -*- coding: utf-8 -*-
"""
Journal text must never reach the logs.

Notes are Fernet-encrypted at rest (src/functions/encrypt.py), so writing the
decrypted body - or the model's summary of it - to a log file would quietly
undo that. Each test plants a sentinel word in the entry (and in the fake
model's summary) and asserts it appears in no log line at any level.
"""

import json
from types import SimpleNamespace

import pytest
from loguru import logger

from src.functions import ai

SENTINEL = "zqxjournalsentinel"


@pytest.fixture
def captured_logs():
    lines = []
    sink_id = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def fake_ai(monkeypatch):
    """An OpenAI client whose summary echoes the sentinel back, like a real
    model restating the entry."""
    reply = {
        "person_names": [],
        "tags": ["reflection"],
        "summary": f"{SENTINEL} day",
        "mood_analysis": "content",
        "mood": "positive",
    }

    async def create(**kwargs):
        message = SimpleNamespace(content=json.dumps(reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    monkeypatch.setattr(
        ai, "_client", SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )


def _assert_no_sentinel(lines):
    assert lines, "no log output captured - the assertion below would be vacuous"
    leaked = [line.strip() for line in lines if SENTINEL in line.lower()]
    assert not leaked, f"journal text reached the log: {leaked}"


def test_get_analysis_does_not_log_entry_text(fake_ai, captured_logs):
    import asyncio

    analysis = asyncio.run(ai.get_analysis(content=f"Today I wrote {SENTINEL}."))
    assert SENTINEL in analysis["summary"].lower()  # the fake really did echo it
    _assert_no_sentinel(captured_logs)


def test_create_note_does_not_log_entry_text(logged_in_client, fake_ai, captured_logs):
    response = logged_in_client.post(
        "/notes/new",
        data={"mood": "positive", "note": f"Today I wrote {SENTINEL}."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _assert_no_sentinel(captured_logs)


def test_csv_import_export_format_does_not_log_entry_text(
    logged_in_client, captured_logs
):
    csv_content = (
        "PKID,User ID,Mood,Mood Analysis,Note,Summary,Tags,Word Count,"
        "Character Count,Date Created\n"
        f'abc123,user-x,positive,happy,"Imported {SENTINEL}.","{SENTINEL} sum",'
        "\"['tag1']\",2,20,01/20/2024\n"
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _assert_no_sentinel(captured_logs)


def test_csv_import_simple_format_ai_path_does_not_log_entry_text(
    logged_in_client, fake_ai, captured_logs
):
    # The simple format is the one that runs each row through the AI
    # (note_import.process_note), which used to log the whole analysis.
    csv_content = (
        f'my_note,mood,date_created\n"Imported {SENTINEL}.",positive,01/20/2024\n'
    )
    response = logged_in_client.post(
        "/notes/bulk",
        files={"csv_file": ("notes.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _assert_no_sentinel(captured_logs)
