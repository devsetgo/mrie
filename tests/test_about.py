# -*- coding: utf-8 -*-
"""
About page edit + image upload coverage (src/endpoints/about.py). Viewing
/about itself is already covered in test_main.py.
"""

import base64
from html.parser import HTMLParser

import pytest

# A minimal valid 1x1 transparent PNG.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


BREAKOUT = "</textarea><script>alert('xss')</script>"
RICH_TEXT = "<p>Fish &amp; chips, <strong>and</strong> a &lt;tag&gt;</p>"


class _ContentTextarea(HTMLParser):
    """Reads the content textarea the way a browser does (entities decoded)."""

    def __init__(self):
        super().__init__()
        self.value = None
        self._inside = False

    def handle_starttag(self, tag, attrs):
        if tag == "textarea" and dict(attrs).get("name") == "content":
            self._inside = True
            self.value = ""

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.value += data


def _save_and_reload_editor(client, content):
    response = client.post(
        "/about/edit", data={"content": content}, follow_redirects=False
    )
    assert response.status_code == 303
    page = client.get("/about/edit")
    assert page.status_code == 200
    parser = _ContentTextarea()
    parser.feed(page.text)
    return page.text, parser.value


def test_content_cannot_break_out_of_the_edit_textarea(logged_in_client):
    html, _ = _save_and_reload_editor(logged_in_client, f"before {BREAKOUT} after")
    assert "<script>alert('xss')</script>" not in html


@pytest.mark.parametrize(
    "content",
    [RICH_TEXT, f"before {BREAKOUT} after", "plain text, no markup"],
    ids=["rich-text", "hostile", "plain"],
)
def test_edit_textarea_round_trips_the_stored_content(logged_in_client, content):
    _, loaded = _save_and_reload_editor(logged_in_client, content)
    assert loaded == content


def test_about_edit_requires_login(client):
    response = client.get("/about/edit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/users/login"


def test_about_edit_form(logged_in_client):
    response = logged_in_client.get("/about/edit")
    assert response.status_code == 200


def test_save_about_updates_content(logged_in_client):
    client = logged_in_client
    unique_content = "<p>pytest updated the about page content.</p>"

    response = client.post(
        "/about/edit", data={"content": unique_content}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/about"

    view = client.get("/about")
    assert "pytest updated the about page content." in view.text


def test_upload_about_image_rejects_unsupported_type(logged_in_client):
    response = logged_in_client.post(
        "/about/upload-image",
        files={"fileToUpload": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_upload_about_image_rejects_oversized_file(logged_in_client):
    oversized = b"a" * (5 * 1024 * 1024 + 1)
    response = logged_in_client.post(
        "/about/upload-image",
        files={"fileToUpload": ("big.png", oversized, "image/png")},
    )
    assert response.status_code == 413
    assert response.json()["success"] is False


def test_upload_about_image_success(logged_in_client):
    response = logged_in_client.post(
        "/about/upload-image",
        files={"fileToUpload": ("tiny.png", _TINY_PNG, "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["url"].startswith("/about/images/")


def test_uploaded_image_is_served_publicly(logged_in_client):
    client = logged_in_client
    upload = client.post(
        "/about/upload-image",
        files={"fileToUpload": ("tiny.png", _TINY_PNG, "image/png")},
    )
    image_url = upload.json()["url"]

    # The About page is public, so its images must load without a session.
    client.cookies.clear()
    response = client.get(image_url)
    assert response.status_code == 200
    assert response.content == _TINY_PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in response.headers["cache-control"]


def test_unknown_image_returns_404(client):
    response = client.get(
        "/about/images/00000000-0000-0000-0000-000000000000",
        headers={"accept": "application/json"},
    )
    assert response.status_code == 404


_GENERATED = {
    "headline": "Builder of <calm> platforms",
    "intro": ["I like hard problems."],
    "sections": [
        {
            "heading": "What I do",
            "paragraphs": ["Lead teams."],
            "highlights": ["Python", "<script>alert(1)</script>"],
        }
    ],
}


@pytest.fixture
def fake_about_ai(monkeypatch):
    from src.functions import ai

    calls = []

    async def fake(resume_text, voice="first", focus=""):
        calls.append({"text": resume_text, "voice": voice, "focus": focus})
        return _GENERATED

    monkeypatch.setattr(ai, "get_about_from_resume", fake)
    return calls


def test_generate_from_pasted_text_returns_escaped_html(
    logged_in_client, fake_about_ai
):
    response = logged_in_client.post(
        "/about/generate-from-resume",
        data={"resume_text": "  CTO at Example  ", "voice": "third", "focus": "py"},
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert "<h2>Builder of &lt;calm&gt; platforms</h2>" in html
    assert "<h3>What I do</h3>" in html
    assert "<li>Python</li>" in html
    assert "<script>" not in html
    assert fake_about_ai == [
        {"text": "CTO at Example", "voice": "third", "focus": "py"}
    ]


def test_generate_from_uploaded_file_prefers_the_file(logged_in_client, fake_about_ai):
    response = logged_in_client.post(
        "/about/generate-from-resume",
        data={"resume_text": "ignored"},
        files={"resume_file": ("cv.txt", b"Staff engineer", "text/plain")},
    )
    assert response.status_code == 200
    assert fake_about_ai[0]["text"] == "Staff engineer"


def test_generate_does_not_save_the_page(logged_in_client, fake_about_ai):
    before = logged_in_client.get("/about").text
    logged_in_client.post("/about/generate-from-resume", data={"resume_text": "x"})
    assert "Builder of" not in logged_in_client.get("/about").text
    assert logged_in_client.get("/about").text == before


def test_generate_requires_input(logged_in_client, fake_about_ai):
    response = logged_in_client.post("/about/generate-from-resume", data={})
    assert response.status_code == 400
    assert fake_about_ai == []


def test_generate_rejects_unreadable_file(logged_in_client, fake_about_ai):
    response = logged_in_client.post(
        "/about/generate-from-resume",
        files={"resume_file": ("cv.png", b"\x89PNG", "image/png")},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["message"]


def test_generate_rejects_oversized_file(logged_in_client, fake_about_ai):
    response = logged_in_client.post(
        "/about/generate-from-resume",
        files={"resume_file": ("cv.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "error,status",
    [(RuntimeError("no key"), 503), (TimeoutError("slow"), 502)],
    ids=["ai-unconfigured", "ai-failed"],
)
def test_generate_reports_ai_failures(logged_in_client, monkeypatch, error, status):
    from src.functions import ai

    async def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(ai, "get_about_from_resume", boom)
    response = logged_in_client.post(
        "/about/generate-from-resume", data={"resume_text": "x"}
    )
    assert response.status_code == status
    assert response.json()["success"] is False


def test_generate_rejects_an_empty_draft(logged_in_client, monkeypatch):
    from src.functions import ai

    async def empty(*args, **kwargs):
        return {"headline": "Hi", "intro": [], "sections": []}

    monkeypatch.setattr(ai, "get_about_from_resume", empty)
    response = logged_in_client.post(
        "/about/generate-from-resume", data={"resume_text": "x"}
    )
    assert response.status_code == 502


def test_generate_requires_login(client):
    response = client.post(
        "/about/generate-from-resume",
        data={"resume_text": "x"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 401


def test_about_page_shows_headshot(client):
    assert "img/headshot_2.jpg" in client.get("/about").text.split("about-photo")[-1]


def test_about_row_creation_failure_is_reported(monkeypatch):
    import asyncio

    from dsg_lib.async_database_functions.database_operations import (
        DatabaseErrorResult,
    )

    from src.endpoints import about

    async def no_row(query):
        return None

    async def failing_insert(query):
        return DatabaseErrorResult({"error": "disk full"})

    monkeypatch.setattr(about.db_ops, "read_one_record", no_row)
    monkeypatch.setattr(about.db_ops, "execute_one", failing_insert)

    with pytest.raises(RuntimeError, match="Failed to create the About page row"):
        asyncio.run(about._get_or_create_about())


def test_upload_reports_a_database_failure_as_500(logged_in_client, monkeypatch):
    from dsg_lib.async_database_functions.database_operations import (
        DatabaseErrorResult,
    )

    from src.endpoints import about

    async def failing_insert(query):
        return DatabaseErrorResult({"error": "disk full"})

    monkeypatch.setattr(about.db_ops, "execute_one", failing_insert)

    response = logged_in_client.post(
        "/about/upload-image",
        files={"fileToUpload": ("a.png", _TINY_PNG, "image/png")},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    # The database's own message is logged, never sent to the browser.
    assert "disk full" not in response.text
