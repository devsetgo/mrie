"""
About page edit + image upload coverage (src/endpoints/about.py). Viewing
/about itself is already covered in test_main.py.

upload_about_image writes to a real path on disk (static/uploads/about/,
gitignored - see CLAUDE.md) rather than the database, so this is the one
place in the test suite with a real filesystem side effect; each test that
uploads a file removes it again in a `finally` block to avoid littering the
repo working tree.
"""
import base64
import os

# A minimal valid 1x1 transparent PNG.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["url"].startswith("/statics/uploads/about/")
    finally:
        uploaded_path = os.path.join(
            "static", "uploads", "about", os.path.basename(body["url"])
        )
        if os.path.exists(uploaded_path):
            os.remove(uploaded_path)
