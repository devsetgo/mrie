"""
Weblinks CRUD coverage. Unlike notes, create_link/edit_weblink call the AI
functions inline (not backgrounded) with no try/except around them, and
capture_full_page_screenshot needs a real Chrome/selenium install this test
environment doesn't have - so both are mocked here rather than skipped, to
keep the test deterministic, fast, and network-free (no real request should
ever leave the test process).
"""
import pytest


@pytest.fixture
def mock_weblink_externals(monkeypatch):
    async def fake_get_url_summary(url, temperature=None, sentence_length=1, **kwargs):
        return {"summary": f"Mock summary for {url}"}

    async def fake_get_url_title(url, temperature=0.7, sentence_length=1, **kwargs):
        return "Mock Title"

    async def fake_get_html_title(url):
        return "Not Found"

    async def fake_capture_full_page_screenshot(url, pkid):
        return None

    async def fake_url_status(url):
        return True

    monkeypatch.setattr("src.functions.ai.get_url_summary", fake_get_url_summary)
    monkeypatch.setattr("src.functions.ai.get_url_title", fake_get_url_title)
    monkeypatch.setattr("src.functions.ai.get_html_title", fake_get_html_title)
    monkeypatch.setattr(
        "src.functions.link_preview.capture_full_page_screenshot",
        fake_capture_full_page_screenshot,
    )
    monkeypatch.setattr("src.functions.link_preview.url_status", fake_url_status)


def _pkid_from_redirect(location: str) -> str:
    return location.rstrip("/").rsplit("/", 1)[-1]


def test_weblinks_requires_login(client):
    response = client.post(
        "/weblinks/new",
        data={"category": "Programming", "url": "https://example.com", "comment": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/users/login"


def test_create_view_update_comment_delete_weblink(
    logged_in_client, mock_weblink_externals
):
    client = logged_in_client

    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/pytest",
            "comment": "",
        },
        follow_redirects=False,
    )
    assert create.status_code == 302
    pkid = _pkid_from_redirect(create.headers["location"])

    view = client.get(f"/weblinks/view/{pkid}")
    assert view.status_code == 200
    assert "Mock Title" in view.text

    update = client.post(
        f"/weblinks/update/comment/{pkid}",
        data={
            "comment": "pytest updated comment",
            "url": "https://example.com/pytest",
            "category": "Programming",
        },
        follow_redirects=False,
    )
    assert update.status_code == 302

    view_after_update = client.get(f"/weblinks/view/{pkid}")
    assert "pytest updated comment" in view_after_update.text

    delete = client.post(
        f"/weblinks/delete/{pkid}",
        data={"deleteConfirm": "on"},
        follow_redirects=False,
    )
    assert delete.status_code == 302

    view_after_delete = client.get(f"/weblinks/view/{pkid}", follow_redirects=False)
    assert view_after_delete.status_code == 303
    assert view_after_delete.headers["location"] == "/error/404"


def test_get_categories(logged_in_client):
    response = logged_in_client.get("/weblinks/categories")
    assert response.status_code == 200
    categories = response.json()
    assert "Programming" in categories


def test_weblinks_pagination_lists_demo_data(logged_in_client):
    response = logged_in_client.get("/weblinks/pagination?limit=5")
    assert response.status_code == 200


def test_weblinks_index_page(logged_in_client):
    response = logged_in_client.get("/weblinks/")
    assert response.status_code == 200


def test_weblinks_new_form(logged_in_client):
    response = logged_in_client.get("/weblinks/new")
    assert response.status_code == 200


def test_weblinks_bulk_form(logged_in_client):
    response = logged_in_client.get("/weblinks/bulk")
    assert response.status_code == 200


def test_weblinks_bulk_import(logged_in_client, mock_weblink_externals):
    csv_content = "public,url,category\nFalse,https://example.com/bulk-import,Programming\n"
    response = logged_in_client.post(
        "/weblinks/bulk",
        files={"csv_file": ("weblinks.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 302

    listing = logged_in_client.get(
        "/weblinks/pagination", params={"search_term": "Mock Title"}
    )
    assert listing.status_code == 200
    assert "Mock Title" in listing.text


def test_edit_weblink(logged_in_client, mock_weblink_externals):
    client = logged_in_client
    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/edit-target",
            "comment": "",
        },
        follow_redirects=False,
    )
    pkid = _pkid_from_redirect(create.headers["location"])

    edit = client.get(f"/weblinks/update/{pkid}", follow_redirects=False)
    assert edit.status_code == 302
    assert edit.headers["location"] == f"/weblinks/view/{pkid}"


def test_edit_weblink_not_found(logged_in_client, mock_weblink_externals):
    response = logged_in_client.get(
        "/weblinks/update/does-not-exist", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/error/404"


def test_get_update_comment_form(logged_in_client, mock_weblink_externals):
    client = logged_in_client
    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/comment-form",
            "comment": "",
        },
        follow_redirects=False,
    )
    pkid = _pkid_from_redirect(create.headers["location"])

    response = client.get(f"/weblinks/update/comment/{pkid}")
    assert response.status_code == 200


def test_delete_weblink_form(logged_in_client, mock_weblink_externals):
    client = logged_in_client
    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/delete-form",
            "comment": "",
        },
        follow_redirects=False,
    )
    pkid = _pkid_from_redirect(create.headers["location"])

    response = client.get(f"/weblinks/delete/{pkid}")
    assert response.status_code == 200


def test_weblinks_pagination_with_category_filter(logged_in_client):
    response = logged_in_client.get(
        "/weblinks/pagination", params={"category": "Programming"}
    )
    assert response.status_code == 200
