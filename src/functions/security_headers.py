# -*- coding: utf-8 -*-
"""
Security response headers: Content-Security-Policy (with a per-request
nonce), Permissions-Policy and the cross-origin isolation headers.

HSTS, X-Frame-Options, X-Content-Type-Options and Referrer-Policy are not
set here: Cloudflare/Traefik already add them in production, and setting
them twice would send duplicate headers.

Every third-party library is vendored under static/vendor/, so scripts only
ever come from 'self'. Inline <script> blocks must carry
nonce="{{ csp_nonce() }}" (a Jinja global, see src/resources.py) or the
browser refuses to run them once the policy is enforced. Inline event
handlers (onclick=...) can't carry a nonce at all - attach listeners from a
nonce'd script instead.
"""

import secrets
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.settings import settings

CSP_REPORT_PATH = "/csp-report"
# Where src/app_routes.py mounts static/.
STATIC_PREFIX = "/statics/"

# Browser features this site never uses. publickey-credentials-get/-create
# are deliberately absent: their default ("self") is exactly what passkey
# login needs, and listing them with () would break login.
PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "browsing-topics",
        "camera",
        "display-capture",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "usb",
    )
)


def _origin(url: str) -> str | None:
    """Returns scheme://host[:port] for an absolute http(s) URL, else None."""
    parts = urlsplit(url or "")
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def build_csp(nonce: str, plausible_script_src: str = "") -> str:
    """Builds the Content-Security-Policy value for one response."""
    plausible = _origin(plausible_script_src)
    extra = f" {plausible}" if plausible else ""
    directives = [
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'{extra}",
        # Inline style="" attributes are used throughout the templates, and
        # htmx/Trumbowyg/Chart.js set styles at runtime. Style injection is
        # far less dangerous than script injection, so this stays open.
        "style-src 'self' 'unsafe-inline'",
        # data: for the base64 link screenshots; https: for images embedded
        # in note bodies and About content by URL.
        "img-src 'self' data: https:",
        "font-src 'self'",
        # noembed.com is called by Trumbowyg's noembed (embed a URL) plugin;
        # Plausible posts its events back to its own host.
        f"connect-src 'self' https://noembed.com{extra}",
        # YouTube embeds on the weblink view page.
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        # report-uri only, no report-to: Chrome ignores report-uri when
        # report-to is present, and batches report-to deliveries for up to
        # a minute (often lost when the tab closes). report-uri is sent
        # immediately and every browser still supports it.
        f"report-uri {CSP_REPORT_PATH}",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware:
    """
    Pure ASGI middleware (not BaseHTTPMiddleware, which buffers streamed
    responses) that stores a fresh nonce in request.state.csp_nonce and adds
    the security headers to every HTTP response.
    """

    def __init__(self, app: ASGIApp, enforce: bool | None = None):
        self.app = app
        # Read at construction rather than per request so a test can build
        # the middleware in either mode without patching settings.
        self.enforce = settings.csp_enforce if enforce is None else enforce

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        csp_header = (
            "content-security-policy"
            if self.enforce
            else "content-security-policy-report-only"
        )
        csp_value = build_csp(nonce, settings.plausible_script_src)

        is_static = scope.get("path", "").startswith(STATIC_PREFIX)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Pages are rendered per request and journal pages hold
                # private text: no-store keeps them out of the browser cache
                # and back/forward history, so pressing Back after logout
                # can't redisplay a note. Static files (cache-busted with
                # ?v=) and routes that set their own Cache-Control, like
                # /about/images, keep theirs.
                if not is_static and not any(
                    name.lower() == b"cache-control" for name, _ in headers
                ):
                    headers.append((b"cache-control", b"no-store"))
                headers += [
                    (csp_header.encode(), csp_value.encode()),
                    (b"permissions-policy", PERMISSIONS_POLICY.encode()),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Violation reports are public, unauthenticated input: cap what is read and
# logged so a bot posting junk can't fill the log volume.
CSP_REPORT_MAX_BYTES = 16 * 1024
CSP_REPORT_MAX_ENTRIES = 20
_FIELD_MAX_CHARS = 300

# (legacy report-uri key, Reporting API key) for each field worth logging.
_REPORT_FIELDS = {
    "directive": ("effective-directive", "effectiveDirective"),
    "blocked": ("blocked-uri", "blockedURL"),
    "page": ("document-uri", "documentURL"),
    "source": ("source-file", "sourceFile"),
    "line": ("line-number", "lineNumber"),
}


def summarize_csp_reports(payload) -> list[dict]:
    """
    Normalizes a CSP violation payload into a short list of dicts.

    Browsers send two shapes: the legacy report-uri body
    ({"csp-report": {...}}, kebab-case keys) and the Reporting API's
    report-to body ([{"type": "csp-violation", "body": {...}}, ...],
    camelCase keys). Anything else is ignored.
    """
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        bodies = [payload["csp-report"]]
    elif isinstance(payload, list):
        bodies = [
            item["body"]
            for item in payload
            if isinstance(item, dict)
            and item.get("type") == "csp-violation"
            and isinstance(item.get("body"), dict)
        ]
    else:
        return []

    summaries = []
    for body in bodies[:CSP_REPORT_MAX_ENTRIES]:
        summary = {}
        for name, keys in _REPORT_FIELDS.items():
            value = next((body[k] for k in keys if body.get(k) not in (None, "")), "")
            summary[name] = str(value)[:_FIELD_MAX_CHARS]
        summaries.append(summary)
    return summaries
