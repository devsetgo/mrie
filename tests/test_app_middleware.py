# -*- coding: utf-8 -*-
"""Header redaction for AccessLoggerMiddleware's request log."""

from starlette.datastructures import Headers

from src.app_middleware import redact_headers


def test_redact_headers_hides_credentials_as_starlette_delivers_them():
    # ASGI servers hand header names over lowercased, which is why an exact
    # "Authorization" match used to redact nothing.
    headers = Headers(
        raw=[
            (b"authorization", b"Bearer secret"),
            (b"cookie", b"session=signed-session-value"),
            (b"x-bootstrap-token", b"bootstrap-secret"),
            (b"accept", b"text/html"),
        ]
    )

    redacted = redact_headers(headers.items())

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["cookie"] == "[REDACTED]"
    assert redacted["x-bootstrap-token"] == "[REDACTED]"
    assert redacted["accept"] == "text/html"


def test_redact_headers_is_case_insensitive():
    redacted = redact_headers([("Cookie", "session=abc")])
    assert redacted == {"Cookie": "[REDACTED]"}
