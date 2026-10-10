# -*- coding: utf-8 -*-
"""
Link title/summary: ai.get_link_analysis (one JSON call, fed the fetched
page) and link_analysis (where the page content comes from, and the
background task that stores the result). The OpenAI client and page fetch
are faked - nothing leaves the process.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import Select

from src.db_tables import WEBLINK_PROCESSING, WebLinks, compute_weblink_ai_fix
from src.functions import ai, link_analysis
from src.functions.page_fetch import PageContent
from src.resources import db_ops


class _FakeCompletions:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_client(monkeypatch):
    completions = _FakeCompletions(
        {"title": "Clean Title", "summary": "What the page covers."}
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(ai, "require_client", lambda feature: client)
    return completions


def _user_message(call):
    return next(m["content"] for m in call["messages"] if m["role"] == "user")


def test_get_link_analysis_sends_the_page_content_in_one_json_call(fake_client):
    page = PageContent(
        url="https://example.com/a",
        title="Raw Title | Site",
        description="A description.",
        text="The article body.",
    )

    result = asyncio.run(ai.get_link_analysis("https://example.com/a", page))

    assert result == {"title": "Clean Title", "summary": "What the page covers."}
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    user = _user_message(call)
    for expected in ("https://example.com/a", "Raw Title | Site", "The article body."):
        assert expected in user
    system = call["messages"][0]["content"]
    assert "never instructions" in system  # page text is data, not a prompt


def test_get_link_analysis_says_when_the_page_is_unavailable(fake_client):
    asyncio.run(ai.get_link_analysis("https://example.com/a", None))

    assert "could not be fetched" in _user_message(fake_client.calls[0])


def test_get_link_analysis_title_falls_back_to_the_page_title(fake_client):
    fake_client.reply = {"title": "", "summary": "S."}
    page = PageContent(url="https://example.com/a", title="Page's Own Title")

    result = asyncio.run(ai.get_link_analysis("https://example.com/a", page))

    assert result["title"] == "Page's Own Title"


def test_get_link_analysis_strips_only_surrounding_quotes(fake_client):
    fake_client.reply = {"title": '"Python\'s Data Model"', "summary": "S."}

    result = asyncio.run(ai.get_link_analysis("https://example.com/a", None))

    assert result["title"] == "Python's Data Model"


def test_analyze_link_uses_youtube_metadata_instead_of_fetching(
    fake_client, monkeypatch
):
    async def fake_metadata(video_id):
        return {
            "title": "A Talk - YouTube",
            "description": "YouTube video by Chan: A Talk",
            "author_name": "Chan",
            "thumbnail_url": "",
        }

    async def no_fetch(url):
        raise AssertionError("YouTube links must not be fetched")

    monkeypatch.setattr(link_analysis, "get_youtube_metadata", fake_metadata)
    monkeypatch.setattr(link_analysis, "fetch_page", no_fetch)

    asyncio.run(
        link_analysis.analyze_link("https://www.youtube.com/watch?v=abcdefghijk")
    )

    assert "A Talk - YouTube" in _user_message(fake_client.calls[0])


def test_ai_fix_treats_the_processing_placeholder_as_missing():
    assert compute_weblink_ai_fix(
        image_preview_data=b"png", title=WEBLINK_PROCESSING, summary="S"
    )
    assert compute_weblink_ai_fix(
        image_preview_data=b"png", title="T", summary=WEBLINK_PROCESSING
    )
    assert not compute_weblink_ai_fix(image_preview_data=b"png", title="T", summary="S")


def _create_placeholder_link(client, monkeypatch):
    """Create a link through the app with the background task disabled."""

    async def skip_background(pkid, url, capture_screenshot=True):
        return None

    with monkeypatch.context() as scoped:
        scoped.setattr(link_analysis, "process_link_background", skip_background)
        create = client.post(
            "/weblinks/new",
            data={
                "category": "Programming",
                "url": "https://example.com/bg",
                "comment": "",
            },
            follow_redirects=False,
        )
    assert create.status_code == 303
    return create.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _read_link(client, pkid):
    # The in-memory DB lives on the app's event loop, so read through the
    # app's portal rather than a fresh asyncio.run().
    return client.portal.call(
        db_ops.read_one_record, Select(WebLinks).where(WebLinks.pkid == pkid)
    )


def _run_background(client, **kwargs):
    client.portal.call(lambda: link_analysis.process_link_background(**kwargs))


def test_new_link_starts_as_a_flagged_placeholder(logged_in_client, monkeypatch):
    pkid = _create_placeholder_link(logged_in_client, monkeypatch)

    record = _read_link(logged_in_client, pkid)
    assert record.title == WEBLINK_PROCESSING
    assert record.summary == WEBLINK_PROCESSING
    assert record.ai_fix is True

    page = logged_in_client.get(f"/weblinks/view/{pkid}")
    assert "fa-spinner" in page.text
    assert "location.reload" in page.text


def test_process_link_background_stores_the_analysis(logged_in_client, monkeypatch):
    pkid = _create_placeholder_link(logged_in_client, monkeypatch)

    async def fake_analyze(url):
        return {"title": "Real Title", "summary": "Real summary."}

    async def fake_screenshot(url, pkid):
        return None

    monkeypatch.setattr(link_analysis, "analyze_link", fake_analyze)
    monkeypatch.setattr(
        link_analysis.link_preview, "capture_full_page_screenshot", fake_screenshot
    )
    _run_background(logged_in_client, pkid=pkid, url="https://example.com/bg")

    record = _read_link(logged_in_client, pkid)
    assert record.title == "Real Title"
    assert record.summary == "Real summary."
    assert "location.reload" not in logged_in_client.get(f"/weblinks/view/{pkid}").text


@pytest.mark.parametrize(
    "error", [RuntimeError("AI disabled"), ValueError("boom")], ids=["no-ai", "crash"]
)
def test_process_link_background_keeps_placeholders_on_failure(
    logged_in_client, monkeypatch, error
):
    pkid = _create_placeholder_link(logged_in_client, monkeypatch)

    async def failing_analyze(url):
        raise error

    monkeypatch.setattr(link_analysis, "analyze_link", failing_analyze)
    # Must not raise - it runs as a background task.
    _run_background(
        logged_in_client,
        pkid=pkid,
        url="https://example.com/bg",
        capture_screenshot=False,
    )

    record = _read_link(logged_in_client, pkid)
    assert record.title == WEBLINK_PROCESSING
    assert record.ai_fix is True


def test_process_link_background_tolerates_a_deleted_link(
    logged_in_client, monkeypatch
):
    async def fake_analyze(url):
        return {"title": "T", "summary": "S"}

    monkeypatch.setattr(link_analysis, "analyze_link", fake_analyze)
    # No such row: must return quietly, not raise.
    _run_background(
        logged_in_client,
        pkid="00000000-0000-0000-0000-000000000000",
        url="https://example.com/gone",
        capture_screenshot=False,
    )


@pytest.mark.parametrize(
    "error",
    [RuntimeError("selenium not installed"), ValueError("unsafe"), OSError("x")],
    ids=["no-selenium", "unsafe-url", "crash"],
)
def test_screenshot_failures_do_not_escape_the_background_task(
    logged_in_client, monkeypatch, error
):
    pkid = _create_placeholder_link(logged_in_client, monkeypatch)

    async def fake_analyze(url):
        return {"title": "Real Title", "summary": "Real summary."}

    async def failing_screenshot(url, pkid):
        raise error

    monkeypatch.setattr(link_analysis, "analyze_link", fake_analyze)
    monkeypatch.setattr(
        link_analysis.link_preview, "capture_full_page_screenshot", failing_screenshot
    )
    _run_background(logged_in_client, pkid=pkid, url="https://example.com/bg")

    # The analysis still landed even though the screenshot failed.
    assert _read_link(logged_in_client, pkid).title == "Real Title"
