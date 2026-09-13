# -*- coding: utf-8 -*-
"""
Direct unit tests for src/db_tables.py.

The ORM event listeners (before_insert_listener/before_update_listener for
WebLinks, note_on_change for Notes) only fire through a real SQLAlchemy
ORM session.add()+flush - nothing in this codebase does that anymore
(every write goes through db_ops.execute_one() with a Core-level
insert()/update() statement; see compute_weblink_ai_fix's and
compute_note_derived_fields's docstrings for why that migration left
these listeners stranded). They're kept as a safety net for any future
ORM-session code path rather than deleted, so - unlike the confirmed-dead
functions removed from notes_metrics.py - they're unit-tested directly
here by calling the listener functions with a constructed instance,
exactly what SQLAlchemy would do internally on a real flush.
"""

from src.db_tables import (
    AboutPage,
    Notes,
    Notifications,
    Users,
    WebAuthnCredentials,
    WebLinks,
    before_insert_listener,
    before_update_listener,
    note_on_change,
)


def test_users_full_name():
    user = Users(first_name="Ada", last_name="Lovelace")
    assert user.full_name == "Ada Lovelace"


def test_users_to_dict():
    user = Users(user_name="ada", first_name="Ada", last_name="Lovelace")
    data = user.to_dict()
    assert data["user_name"] == "ada"
    assert data["first_name"] == "Ada"


def test_webauthn_credentials_to_dict():
    cred = WebAuthnCredentials(
        user_id="user-1", credential_id="cred-1", public_key=b"key"
    )
    data = cred.to_dict()
    assert data["user_id"] == "user-1"
    assert data["credential_id"] == "cred-1"


def test_about_page_to_dict():
    page = AboutPage(content="<p>Hello</p>")
    assert page.to_dict()["content"] == "<p>Hello</p>"


def test_notifications_to_dict():
    notification = Notifications(user_id="user-1", message="hello", category="info")
    data = notification.to_dict()
    assert data["message"] == "hello"
    assert data["category"] == "info"


def test_weblinks_is_youtube_false_for_no_url():
    assert WebLinks(url=None).is_youtube is False


def test_weblinks_is_youtube_true_for_youtube_url():
    assert WebLinks(url="https://youtube.com/watch?v=abc").is_youtube is True


def test_weblinks_update_ai_fix_true_when_image_missing():
    link = WebLinks(title="Title", summary="Summary", image_preview_data=None)
    link.update_ai_fix()
    assert link.ai_fix is True


def test_weblinks_update_ai_fix_false_when_complete():
    link = WebLinks(title="Title", summary="Summary", image_preview_data=b"fake-bytes")
    link.update_ai_fix()
    assert link.ai_fix is False


def test_before_insert_and_before_update_listeners_delegate_to_update_ai_fix():
    link = WebLinks(title=None, summary=None, image_preview_data=None)
    before_insert_listener(mapper=None, connection=None, target=link)
    assert link.ai_fix is True

    link.title = "Now set"
    link.summary = "Now set"
    link.image_preview_data = b"fake-bytes"
    before_update_listener(mapper=None, connection=None, target=link)
    assert link.ai_fix is False


def test_note_on_change_listener_computes_derived_fields():
    note_text = "This is a clean note with no odd characters."
    note = Notes(
        mood="positive",
        note=note_text,
        summary="A short summary.",
        mood_analysis="content",
        tags=["clean", "tags"],
        demo_created=0,
    )
    note_on_change(mapper=None, connection=None, target=note)
    assert note.word_count == len(note_text.split())
    assert note.character_count == len(note_text)
    assert note.ai_fix is False
    assert note.demo_created == 0


def test_note_on_change_listener_flags_ai_fix_for_bad_tag():
    note = Notes(
        mood="positive",
        note="note text",
        summary="summary",
        mood_analysis="content",
        tags=["bad tag with space"],
        demo_created=0,
    )
    note_on_change(mapper=None, connection=None, target=note)
    assert note.ai_fix is True


def test_note_on_change_listener_flags_ai_fix_for_mood_analysis_with_space():
    note = Notes(
        mood="positive",
        note="note text",
        summary="summary",
        mood_analysis="not a single word",
        tags=[],
        demo_created=0,
    )
    note_on_change(mapper=None, connection=None, target=note)
    assert note.ai_fix is True


def test_note_on_change_listener_bumps_demo_created():
    note = Notes(
        mood="positive",
        note="note text",
        summary="summary",
        mood_analysis="content",
        tags=[],
        demo_created=1,
    )
    note_on_change(mapper=None, connection=None, target=note)
    assert note.ai_fix is True
    assert note.demo_created == 2


def test_notes_note_property_returns_error_string_on_decryption_failure():
    note = Notes()
    note._note = b"not-a-valid-fernet-token"
    assert "Failed to decrypt note" in note.note


def test_notes_summary_property_returns_error_string_on_decryption_failure():
    note = Notes()
    note._summary = b"not-a-valid-fernet-token"
    assert "Failed to decrypt summary" in note.summary


def test_notes_note_setter_swallows_encryption_error():
    note = Notes()
    # encrypt_text() calls value.encode("utf-8") internally - a non-str
    # value raises AttributeError there, which becomes an EncryptionError
    # the setter catches (and logs) rather than letting propagate.
    note.note = 12345
    assert note._note is None


def test_notes_summary_setter_swallows_encryption_error():
    note = Notes()
    note.summary = 12345
    assert note._summary is None
