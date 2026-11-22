# -*- coding: utf-8 -*-
"""
Content-Security-Policy and the other security headers
(src/functions/security_headers.py), and the vendored assets the policy
depends on.
"""

import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from src.functions.security_headers import (
    CSP_REPORT_MAX_BYTES,
    SecurityHeadersMiddleware,
    build_csp,
    summarize_csp_reports,
)

CSP_REPORT_ONLY = "content-security-policy-report-only"
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.I)
NONCE_IN_POLICY = re.compile(r"'nonce-([^']+)'")


def _nonce(response) -> str:
    return NONCE_IN_POLICY.search(response.headers[CSP_REPORT_ONLY]).group(1)


def _assert_every_script_has_nonce(response):
    nonce = _nonce(response)
    tags = SCRIPT_TAG.findall(response.text)
    assert tags, "expected the page to contain <script> tags"
    for tag in tags:
        assert f'nonce="{nonce}"' in tag, tag


def _assert_no_third_party_assets(response):
    # Every script and stylesheet is vendored under /statics/vendor; a CDN
    # URL here would be blocked by script-src 'self' once enforced.
    for tag in re.findall(r"<(?:script|link)\b[^>]*>", response.text, re.I):
        url = re.search(r'\b(?:src|href)="(https?://[^"]+)"', tag)
        if url and ("<script" in tag.lower() or "stylesheet" in tag):
            assert url.group(1).startswith("http://testserver/"), tag


def test_public_page_sends_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    policy = response.headers[CSP_REPORT_ONLY]
    assert "content-security-policy" not in response.headers  # report-only by default
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "report-uri /csp-report" in policy
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "camera=()" in response.headers["permissions-policy"]


def test_permissions_policy_leaves_passkeys_alone(client):
    # Listing publickey-credentials-* with () would disable passkey login.
    policy = client.get("/").headers["permissions-policy"]
    assert "publickey-credentials" not in policy


def test_nonce_is_fresh_per_request(client):
    first = _nonce(client.get("/"))
    second = _nonce(client.get("/"))
    assert first != second


def test_redirects_and_error_pages_get_headers_too(client):
    response = client.get("/notes/", follow_redirects=False)
    assert response.status_code == 303
    assert CSP_REPORT_ONLY in response.headers
    assert CSP_REPORT_ONLY in client.get("/error/404").headers


@pytest.mark.parametrize("path", ["/", "/about", "/users/login", "/error/404"])
def test_public_pages_nonce_every_script(client, path):
    response = client.get(path)
    assert response.status_code == 200
    _assert_every_script_has_nonce(response)
    _assert_no_third_party_assets(response)


@pytest.mark.parametrize(
    "path",
    [
        "/notes/",
        "/notes/list",
        "/notes/new",
        "/notes/bulk",
        "/weblinks/",
        "/weblinks/new",
        "/weblinks/bulk",
        "/about/edit",
    ],
)
def test_app_pages_nonce_every_script(logged_in_client, path):
    response = logged_in_client.get(path)
    assert response.status_code == 200
    _assert_every_script_has_nonce(response)
    _assert_no_third_party_assets(response)
    # Inline handlers can't carry a nonce, so the policy would block them.
    assert not re.search(r"<[^>]+\son[a-z]+=", response.text, re.I)


def test_vendored_assets_referenced_by_templates_exist():
    referenced = set()
    for template in pathlib.Path("templates").rglob("*.html"):
        referenced |= set(re.findall(r"path='(vendor/[^']+)'", template.read_text()))
    assert referenced
    missing = [p for p in sorted(referenced) if not pathlib.Path("static", p).is_file()]
    assert missing == []


def test_vendored_asset_is_served(client):
    response = client.get("/statics/vendor/htmx-2.0.3/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:2000]


def test_build_csp_allows_plausible_origin_only():
    policy = build_csp("abc", "https://plausible.example.com/js/script.js")
    assert "script-src 'self' 'nonce-abc' https://plausible.example.com;" in policy
    assert "https://plausible.example.com/js" not in policy


@pytest.mark.parametrize("src", ["", "javascript:alert(1)", "/js/script.js"])
def test_build_csp_ignores_unusable_plausible_src(src):
    assert "script-src 'self' 'nonce-abc';" in build_csp("abc", src)


@pytest.mark.parametrize(
    "enforce,header",
    [(True, "content-security-policy"), (False, CSP_REPORT_ONLY)],
)
def test_enforce_flag_picks_header(enforce, header):
    app = FastAPI()

    @app.get("/")
    async def _root():
        return {"ok": True}

    app.add_middleware(SecurityHeadersMiddleware, enforce=enforce)
    response = TestClient(app).get("/")
    assert header in response.headers
    other = CSP_REPORT_ONLY if enforce else "content-security-policy"
    assert other not in response.headers


def test_summarize_legacy_report():
    payload = {
        "csp-report": {
            "document-uri": "https://mikeryan.ie/notes/",
            "effective-directive": "script-src-elem",
            "blocked-uri": "https://evil.example/x.js",
            "line-number": 12,
        }
    }
    assert summarize_csp_reports(payload) == [
        {
            "directive": "script-src-elem",
            "blocked": "https://evil.example/x.js",
            "page": "https://mikeryan.ie/notes/",
            "source": "",
            "line": "12",
        }
    ]


def test_summarize_reporting_api_and_ignores_other_types():
    payload = [
        {"type": "deprecation", "body": {"id": "x"}},
        {
            "type": "csp-violation",
            "body": {"effectiveDirective": "img-src", "blockedURL": "http://x/y.png"},
        },
    ]
    [summary] = summarize_csp_reports(payload)
    assert summary["directive"] == "img-src"
    assert summary["blocked"] == "http://x/y.png"


@pytest.mark.parametrize("payload", [None, "text", 5, {"other": 1}, [1, "x"]])
def test_summarize_ignores_junk(payload):
    assert summarize_csp_reports(payload) == []


def test_summarize_truncates_and_caps():
    body = {"blocked-uri": "x" * 5000}
    summaries = summarize_csp_reports([{"type": "csp-violation", "body": body}] * 50)
    assert len(summaries) == 20
    assert len(summaries[0]["blocked"]) == 300


@pytest.fixture
def captured_logs():
    lines = []
    sink_id = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


def test_csp_report_endpoint_logs_violation(client, captured_logs):
    response = client.post(
        "/csp-report",
        content=b'{"csp-report": {"blocked-uri": "inline\\nFAKE LOG LINE"}}',
        headers={"content-type": "application/csp-report"},
    )
    assert response.status_code == 204
    [line] = [entry for entry in captured_logs if "CSP violation" in entry]
    # The value is repr()'d, so an embedded newline can't start a new log line.
    assert "inline\\nFAKE LOG LINE" in line


@pytest.mark.parametrize(
    "body", [b"", b"not json", b'{"csp-report": 1}', b"x" * (CSP_REPORT_MAX_BYTES + 1)]
)
def test_csp_report_endpoint_tolerates_junk(client, captured_logs, body):
    assert client.post("/csp-report", content=body).status_code == 204
    assert not [entry for entry in captured_logs if "CSP violation" in entry]


def test_pages_are_not_stored(logged_in_client):
    for path in ["/", "/notes/", "/weblinks/", "/error/404"]:
        assert logged_in_client.get(path).headers["cache-control"] == "no-store", path
    redirect = logged_in_client.get("/notes", follow_redirects=False)
    assert redirect.headers["cache-control"] == "no-store"


def test_static_files_keep_browser_caching(client):
    response = client.get("/statics/vendor/htmx-2.0.3/htmx.min.js")
    assert response.headers.get("cache-control") != "no-store"


def test_route_cache_control_is_not_overridden():
    from fastapi.responses import Response

    app = FastAPI()

    @app.get("/img")
    async def _img():
        return Response(b"x", headers={"Cache-Control": "public, max-age=60"})

    app.add_middleware(SecurityHeadersMiddleware)
    response = TestClient(app).get("/img")
    assert response.headers.get_list("cache-control") == ["public, max-age=60"]


def test_rendered_pages_carry_no_html_comments(logged_in_client):
    # Template notes are {# Jinja comments #}, stripped at render time; an
    # HTML <!-- comment --> would ship internal details to every visitor.
    for path in ["/", "/about", "/users/login", "/notes/", "/notes/new", "/weblinks/"]:
        assert "<!--" not in logged_in_client.get(path).text, path
