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
