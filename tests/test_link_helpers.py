# -*- coding: utf-8 -*-
"""
Link status checks, the CSV screenshot loop, and the edit-link flow
(src/functions/link_preview.py, src/functions/link_import.py,
src/endpoints/web_links.py::update_comment).
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from src.functions import link_import, link_preview


@pytest.fixture
def status_client(monkeypatch):
    """Route url_status's shared client through a mock transport."""
    monkeypatch.setattr(link_preview, "resolve_public_ip", lambda url: "93.184.216.34")

    def install(handler):
        monkeypatch.setattr(
            link_preview,
            "client",
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    return install


def _status(url="https://example.com/a"):
    return asyncio.run(link_preview.url_status(url))


@pytest.mark.parametrize(
    "status,expected", [(200, True), (301, True), (404, False), (500, False)]
)
def test_url_status_reflects_the_response_code(status_client, status, expected):
    status_client(lambda request: httpx.Response(status))
    assert _status() is expected


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout("t"), httpx.ConnectError("refused"), httpx.ReadTimeout("t")],
    ids=["timeout", "refused", "read-timeout"],
)
def test_url_status_is_false_when_the_host_cant_be_reached(status_client, error):
    def handler(request):
        raise error

    status_client(handler)
    # A dead host used to report True and show "Link Working".
    assert _status() is False


def test_url_status_never_requests_an_unsafe_address(monkeypatch, status_client):
    def handler(request):
        raise AssertionError("must not be requested")

    status_client(handler)
    monkeypatch.setattr(link_preview, "resolve_public_ip", lambda url: None)
    assert _status("http://10.0.0.1/") is False


def test_url_status_connects_to_the_checked_address(status_client):
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        seen["header"] = request.headers["host"]
        return httpx.Response(200)

    status_client(handler)
    assert _status("https://example.com/a") is True
    assert seen == {"host": "93.184.216.34", "header": "example.com"}


# --- CSV screenshot loop ---------------------------------------------------


@pytest.fixture
def record_lookup(monkeypatch):
    async def fake_read_one_record(query):
        return SimpleNamespace(url="https://example.com/x", pkid="p")

    monkeypatch.setattr(link_import.db_ops, "read_one_record", fake_read_one_record)


def _run_loop(pkids):
    asyncio.run(link_import.loop_pkids_for_images(pkids))


def test_screenshot_loop_stops_once_when_selenium_is_missing(
    monkeypatch, record_lookup
):
    calls = []

    async def capture(url, pkid):
        calls.append(pkid)
        raise RuntimeError("selenium/webdriver-manager not installed")

    monkeypatch.setattr(
        link_import.link_preview, "capture_full_page_screenshot", capture
    )
    _run_loop(["a", "b", "c"])
    # Every later link would fail the same way, so it gives up after one.
    assert len(calls) == 1


def test_screenshot_loop_skips_an_unsafe_url_and_carries_on(monkeypatch, record_lookup):
    outcomes = iter([ValueError("unsafe"), None, Exception("boom"), None])
    calls = []

    async def capture(url, pkid):
        calls.append(pkid)
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(
        link_import.link_preview, "capture_full_page_screenshot", capture
    )
    _run_loop(["a", "b", "c", "d"])
    assert len(calls) == 4


def test_screenshot_loop_skips_unreadable_rows(monkeypatch):
    async def fake_read_one_record(query):
        return {"error": "db"}

    async def capture(url, pkid):
        raise AssertionError("must not be called")

    monkeypatch.setattr(link_import.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(
        link_import.link_preview, "capture_full_page_screenshot", capture
    )
    _run_loop(["a"])


# --- edit link -------------------------------------------------------------


@pytest.fixture
def analysis_calls(monkeypatch):
    calls = []

    async def fake_process(pkid, url, capture_screenshot=True):
        calls.append({"pkid": pkid, "url": url})

    monkeypatch.setattr(
        "src.functions.link_analysis.process_link_background", fake_process
    )
    return calls


def _create(client, url="https://example.com/original"):
    response = client.post(
        "/weblinks/new",
        data={"category": "Programming", "url": url, "comment": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _update(client, pkid, **fields):
    data = {
        "comment": "c",
        "url": "https://example.com/original",
        "category": "Programming",
    }
    data.update(fields)
    return client.post(
        f"/weblinks/update/comment/{pkid}",
        data=data,
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )


def test_changing_the_url_reanalyzes_the_link(logged_in_client, analysis_calls):
    pkid = _create(logged_in_client)
    analysis_calls.clear()

    response = _update(logged_in_client, pkid, url="https://example.com/new-page")

    assert response.status_code == 303
    assert analysis_calls == [{"pkid": pkid, "url": "https://example.com/new-page"}]


def test_editing_only_the_comment_does_not_reanalyze(logged_in_client, analysis_calls):
    pkid = _create(logged_in_client)
    analysis_calls.clear()

    response = _update(logged_in_client, pkid, comment="just a note")

    assert response.status_code == 303
    assert analysis_calls == []


@pytest.mark.parametrize("missing", ["comment", "url", "category"])
def test_edit_with_a_missing_field_is_a_400_not_a_500(
    logged_in_client, analysis_calls, missing
):
    pkid = _create(logged_in_client)
    data = {"comment": "c", "url": "https://example.com/original", "category": "x"}
    data.pop(missing)

    response = logged_in_client.post(
        f"/weblinks/update/comment/{pkid}",
        data=data,
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_editing_a_missing_link_redirects_to_404(logged_in_client, analysis_calls):
    response = _update(logged_in_client, "does-not-exist")
    assert response.status_code == 303
    assert response.headers["location"] == "/error/404"


# --- delete / update error branches ----------------------------------------


def test_delete_requires_the_confirmation_box(logged_in_client, analysis_calls):
    pkid = _create(logged_in_client)
    response = logged_in_client.post(
        f"/weblinks/delete/{pkid}", data={}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/error/400"
    # Still there.
    assert logged_in_client.get(f"/weblinks/view/{pkid}").status_code == 200


def test_delete_of_a_missing_link_redirects_to_404(logged_in_client):
    response = logged_in_client.post(
        "/weblinks/delete/does-not-exist",
        data={"deleteConfirm": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/error/404"


def test_delete_database_failure_goes_to_the_error_page(
    logged_in_client, analysis_calls, monkeypatch
):
    from dsg_lib.async_database_functions.database_operations import (
        DatabaseErrorResult,
    )

    from src.endpoints import web_links

    pkid = _create(logged_in_client)

    async def failing(query):
        return DatabaseErrorResult({"error": "locked"})

    monkeypatch.setattr(web_links.db_ops, "execute_one", failing)
    response = logged_in_client.post(
        f"/weblinks/delete/{pkid}",
        data={"deleteConfirm": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/error/500"


def test_update_database_failure_goes_to_the_error_page(
    logged_in_client, analysis_calls, monkeypatch
):
    from dsg_lib.async_database_functions.database_operations import (
        DatabaseErrorResult,
    )

    from src.endpoints import web_links

    pkid = _create(logged_in_client)
    analysis_calls.clear()  # the create above queued its own analysis

    async def failing(query):
        return DatabaseErrorResult({"error": "locked"})

    monkeypatch.setattr(web_links.db_ops, "execute_one", failing)
    response = _update(logged_in_client, pkid, comment="x")

    assert response.status_code == 303
    assert response.headers["location"] == "/error/418"
    # Nothing was queued for a change that never happened.
    assert analysis_calls == []
