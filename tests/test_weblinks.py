# -*- coding: utf-8 -*-
"""
Weblinks CRUD coverage. create_link/edit_weblink hand the page fetch + AI
call to link_analysis.process_link_background (TestClient runs background
tasks before returning), and capture_full_page_screenshot needs a real
Chrome/selenium install this test environment doesn't have - so both are
mocked here rather than skipped, to keep the test deterministic, fast, and
network-free (no real request should ever leave the test process).
"""

import pytest


@pytest.fixture
def mock_weblink_externals(monkeypatch):
    async def fake_analyze_link(url):
        return {"title": "Mock Title", "summary": f"Mock summary for {url}"}

    async def fake_capture_full_page_screenshot(url, pkid):
        return None

    async def fake_url_status(url):
        return True

    monkeypatch.setattr("src.functions.link_analysis.analyze_link", fake_analyze_link)
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
    assert create.status_code == 303
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
    assert update.status_code == 303

    view_after_update = client.get(f"/weblinks/view/{pkid}")
    assert "pytest updated comment" in view_after_update.text

    delete = client.post(
        f"/weblinks/delete/{pkid}",
        data={"deleteConfirm": "on"},
        follow_redirects=False,
    )
    assert delete.status_code == 303

    view_after_delete = client.get(f"/weblinks/view/{pkid}", follow_redirects=False)
    assert view_after_delete.status_code == 303
    assert view_after_delete.headers["location"] == "/error/404"


def test_delete_weblink_rejects_non_owner_non_admin(
    logged_in_client, mock_weblink_externals
):
    # This app only ever has one real identity (single-admin design, see
    # CLAUDE.md) - every real login session has is_admin=True, so a
    # non-owner/non-admin caller can't be produced through the normal login
    # flow. Overriding the check_login dependency directly is the only way
    # to exercise delete_weblink's ownership guard (as opposed to its
    # separate "not found" branch, already covered above).
    from src.functions.login_required import check_login
    from src.main import app

    client = logged_in_client
    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/ownership-test",
            "comment": "",
        },
        follow_redirects=False,
    )
    pkid = _pkid_from_redirect(create.headers["location"])

    async def fake_check_login():
        return {
            "user_identifier": "someone-else",
            "is_admin": False,
            "timezone": None,
            "exp": 0,
        }

    app.dependency_overrides[check_login] = fake_check_login
    try:
        response = client.post(
            f"/weblinks/delete/{pkid}",
            data={"deleteConfirm": "on"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(check_login, None)

    assert response.status_code == 303
    assert response.headers["location"] == "/error/403"

    # Confirm it's actually still there - the guard blocked the delete.
    still_there = client.get(f"/weblinks/view/{pkid}")
    assert still_there.status_code == 200


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
    csv_content = (
        "public,url,category\nFalse,https://example.com/bulk-import,Programming\n"
    )
    response = logged_in_client.post(
        "/weblinks/bulk",
        files={"csv_file": ("weblinks.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303

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

    edit = client.post(f"/weblinks/update/{pkid}", follow_redirects=False)
    assert edit.status_code == 303
    assert edit.headers["location"] == f"/weblinks/view/{pkid}"


def test_edit_weblink_not_found(logged_in_client, mock_weblink_externals):
    response = logged_in_client.post(
        "/weblinks/update/does-not-exist", follow_redirects=False
    )
    assert response.status_code == 303
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


BAD_URLS = [
    "javascript:fetch('/notes/list')",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "example.com/no-scheme",
]


@pytest.mark.parametrize("bad_url", BAD_URLS)
def test_create_weblink_rejects_non_http_urls(
    logged_in_client, mock_weblink_externals, bad_url
):
    response = logged_in_client.post(
        "/weblinks/new",
        data={"category": "Programming", "url": bad_url, "comment": ""},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "http://" in response.json()["detail"]


@pytest.mark.parametrize("bad_url", BAD_URLS)
def test_update_weblink_rejects_non_http_urls_and_keeps_the_old_one(
    logged_in_client, mock_weblink_externals, bad_url
):
    client = logged_in_client
    create = client.post(
        "/weblinks/new",
        data={
            "category": "Programming",
            "url": "https://example.com/keep",
            "comment": "",
        },
        follow_redirects=False,
    )
    pkid = _pkid_from_redirect(create.headers["location"])

    response = client.post(
        f"/weblinks/update/comment/{pkid}",
        data={"comment": "x", "url": bad_url, "category": "Programming"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    view = client.get(f"/weblinks/view/{pkid}")
    assert "https://example.com/keep" in view.text
    assert bad_url not in view.text


def test_bulk_import_skips_rows_without_an_http_url(
    logged_in_client, mock_weblink_externals
):
    csv_content = (
        "public,url,category\n"
        "False,javascript:alert(1),Programming\n"
        "False,https://example.com/good-row,Programming\n"
    )
    response = logged_in_client.post(
        "/weblinks/bulk",
        files={"csv_file": ("weblinks.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    listing = logged_in_client.get("/weblinks/pagination", params={"limit": 100})
    assert "example.com/good-row" in listing.text
    assert "javascript:alert" not in listing.text


def test_safe_href_filter_neutralises_stored_bad_urls():
    from src.resources import templates

    render = templates.env.from_string('<a href="{{ url | safe_href }}">x</a>').render
    assert (
        render(url="https://example.com/a") == '<a href="https://example.com/a">x</a>'
    )
    assert render(url="javascript:alert(1)") == '<a href="#">x</a>'
    assert render(url=None) == '<a href="#">x</a>'


def test_reanalysis_is_post_only(logged_in_client, mock_weblink_externals):
    # A GET can be fired by any page the admin visits; it must not act.
    response = logged_in_client.get(
        "/weblinks/update/anything",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert response.status_code == 405
