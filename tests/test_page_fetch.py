# -*- coding: utf-8 -*-
"""
src/functions/page_fetch.py - the SSRF guard, HTML extraction, and
fetch_page's per-hop checks and limits. No request leaves the process:
fetches go through httpx.MockTransport, and the DNS-dependent guard is
exercised with IP literals (no lookup needed) or swapped for a host
allowlist.
"""

import asyncio

import httpx
import pytest

from src.functions import page_fetch
from src.functions.page_fetch import fetch_page, is_safe_public_url, parse_page

PAGE = """<!doctype html>
<html><head>
  <title>Fallback &amp; Title | Example Site</title>
  <meta name="description" content="What the page is about.">
  <meta property="og:title" content="The Real Title">
  <script>var tracking = "not content";</script>
  <style>body { color: red; }</style>
</head><body>
  <nav>Home About Contact</nav>
  <header>Site banner</header>
  <main><h1>Heading</h1><p>First   paragraph
     of the article.</p></main>
  <footer>Copyright footer</footer>
</body></html>"""


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.42.0.5:5000/",
        "http://192.168.1.10/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://93.184.216.34/",
        "not a url",
    ],
)
def test_is_safe_public_url_rejects_internal_and_non_http(url):
    assert is_safe_public_url(url) is False


def test_is_safe_public_url_allows_a_public_address():
    assert is_safe_public_url("https://93.184.216.34/page") is True


def test_parse_page_extracts_title_description_and_content_text():
    page = parse_page(PAGE, url="https://example.com/a")

    assert page.title == "The Real Title"  # og:title wins over <title>
    assert page.description == "What the page is about."
    assert "Heading" in page.text
    assert "First paragraph of the article." in page.text  # whitespace collapsed
    for chrome in ("not content", "color: red", "Home About", "banner", "footer"):
        assert chrome not in page.text


def test_parse_page_falls_back_to_the_title_tag():
    page = parse_page(
        "<title>Only &amp; Title</title><p>x</p>", url="https://example.com"
    )
    assert page.title == "Only & Title"


def test_parse_page_caps_the_text():
    page = parse_page("<p>" + "word " * 5000 + "</p>", url="https://example.com")
    assert len(page.text) == page_fetch.MAX_TEXT_CHARS


@pytest.fixture
def allow_hosts(monkeypatch):
    """Replace the DNS-based guard with a host allowlist."""
    allowed = {"example.com", "www.example.com"}

    def fake_is_safe(url):
        return httpx.URL(url).host in allowed

    monkeypatch.setattr(page_fetch, "is_safe_public_url", fake_is_safe)
    return allowed


def _fetch(url, handler):
    return asyncio.run(fetch_page(url, transport=httpx.MockTransport(handler)))


def _html(body=PAGE, **headers):
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", **headers},
        text=body,
    )


def test_fetch_page_returns_parsed_content(allow_hosts):
    page = _fetch("https://example.com/a", lambda request: _html())

    assert page.title == "The Real Title"
    assert page.url == "https://example.com/a"


def test_fetch_page_follows_a_safe_redirect(allow_hosts):
    def handler(request):
        if request.url.host == "example.com":
            return httpx.Response(
                301, headers={"location": "https://www.example.com/b"}
            )
        return _html()

    page = _fetch("https://example.com/a", handler)

    assert page is not None
    assert page.url == "https://www.example.com/b"


def test_fetch_page_refuses_a_redirect_to_an_internal_host(allow_hosts):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    assert _fetch("https://example.com/a", handler) is None
    # The metadata endpoint itself was never requested.
    assert requested == ["https://example.com/a"]


def test_fetch_page_refuses_an_unsafe_starting_url(allow_hosts):
    def handler(request):
        raise AssertionError("must not be requested")

    assert _fetch("http://internal.local/", handler) is None


def test_fetch_page_gives_up_after_too_many_redirects(allow_hosts):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    assert _fetch("https://example.com/a", handler) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, headers={"content-type": "text/html"}, text="nope"),
        httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF"
        ),
    ],
    ids=["error-status", "not-html"],
)
def test_fetch_page_returns_none_for_unusable_responses(allow_hosts, response):
    assert _fetch("https://example.com/a", lambda request: response) is None


def test_fetch_page_returns_none_on_network_errors(allow_hosts):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    assert _fetch("https://example.com/a", handler) is None


def test_fetch_page_reads_at_most_max_bytes(allow_hosts, monkeypatch):
    monkeypatch.setattr(page_fetch, "MAX_BYTES", 1000)
    huge = "<title>Big</title><p>" + "a" * 50_000 + "</p>"

    page = _fetch("https://example.com/a", lambda request: _html(huge))

    assert page.title == "Big"
    assert len(page.text) < 1000
