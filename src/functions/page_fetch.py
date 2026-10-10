# -*- coding: utf-8 -*-
"""
Fetches a bookmarked page's title, description and readable text, so link
summaries describe what the page actually says instead of what the model
can guess from the URL string.

Every URL fetched here is admin-supplied, but a compromised or CSRF'd admin
session still shouldn't be able to use "add a bookmark" to reach the
deployment's internal network - so every hop, including each redirect, is
checked with is_safe_public_url() before it's requested, and responses are
capped in size and time.

Author:
    Mike Ryan
    MIT Licensed
"""

import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

import anyio
import httpx
from loguru import logger

_ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 6000
TIMEOUT = httpx.Timeout(10.0)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
}


def normalize_link_url(raw: object) -> Optional[str]:
    """
    Returns the stripped URL if it is an absolute http(s) URL with a host,
    else None. Links are rendered as `<a href>`, and a stored
    `javascript:`/`data:` URL would run script in the admin's origin when
    clicked - autoescaping doesn't neutralise a scheme - so every path that
    stores a link URL goes through this first.
    """
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        return None
    return url


def resolve_public_ip(url: str) -> Optional[str]:
    """
    Returns the address to connect to for `url`, or None if the URL mustn't
    be fetched: a non-http(s) scheme (file://, etc.), or a hostname with
    *any* private/loopback/link-local/reserved address - the cloud metadata
    endpoint (169.254.169.254), internal services (RFC1918), the local
    machine (127.0.0.0/8), and K3s' own pod/service networks.

    Callers should connect to the returned address (see pinned_request())
    rather than let the HTTP client resolve the name a second time: a
    hostile DNS server could otherwise answer "public" here and "internal"
    to the client's own lookup (DNS rebinding).

    Blocking (DNS lookup) - call it via anyio.to_thread from async code.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return None

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror, UnicodeError:
        return None

    addresses = []
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return None
        addresses.append(str(ip))

    return addresses[0] if addresses else None


def is_safe_public_url(url: str) -> bool:
    """True if resolve_public_ip() finds a connectable public address."""
    return resolve_public_ip(url) is not None


def pinned_request(url: str, ip: str) -> tuple[str, dict, dict]:
    """
    Returns (url, headers, extensions) that make httpx connect to `ip`
    while still presenting `url`'s own hostname: the Host header carries the
    name for virtual hosting, and httpcore's `sni_hostname` extension sets
    both the TLS SNI and the name the certificate is verified against, so
    HTTPS validation is unchanged.
    """
    parsed = httpx.URL(url)
    return (
        str(parsed.copy_with(host=ip)),
        {"Host": parsed.netloc.decode("ascii")},
        {"sni_hostname": parsed.host},
    )


@dataclass
class PageContent:
    url: str
    title: str = ""
    description: str = ""
    text: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.description or self.text)


class _PageParser(HTMLParser):
    # Page chrome and non-content elements - their text is menus, cookie
    # banners and code, which only dilutes what the model sees.
    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "select",
    }
    META_KEYS = {
        "description": "description",
        "og:description": "og_description",
        "twitter:description": "og_description",
        "og:title": "og_title",
        "twitter:title": "og_title",
    }

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta = {}
        self._chunks = []
        self._skip_depth = 0
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title" and not self._title_done:
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            key = self.META_KEYS.get((a.get("name") or a.get("property") or "").lower())
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(self._chunks)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_page(html: str, url: str) -> PageContent:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    return PageContent(
        url=url,
        title=_clean(parser.meta.get("og_title") or parser.title)[:300],
        description=_clean(
            parser.meta.get("description") or parser.meta.get("og_description")
        )[:1000],
        text=_clean(parser.text)[:MAX_TEXT_CHARS],
    )


async def fetch_page(
    url: str, transport: Optional[httpx.AsyncBaseTransport] = None
) -> Optional[PageContent]:
    """
    Returns the page's content, or None when it can't or mustn't be fetched
    (unsafe address, error status, not HTML, network failure). Never raises:
    callers fall back to describing the link from its URL alone.

    `transport` exists for tests (httpx.MockTransport).
    """
    current = url
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers=HEADERS,
            follow_redirects=False,  # each hop is safety-checked below
            transport=transport,
        ) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                ip = await anyio.to_thread.run_sync(resolve_public_ip, current)
                if ip is None:
                    logger.warning(f"Refusing to fetch unsafe URL: {current!r}")
                    return None

                # Connect to the address that was just checked, not whatever
                # the client would resolve the name to a moment later.
                request_url, headers, extensions = pinned_request(current, ip)
                async with client.stream(
                    "GET", request_url, headers=headers, extensions=extensions
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        # Join against the logical URL: response.url is the
                        # pinned (IP) form.
                        current = str(httpx.URL(current).join(location))
                        continue

                    if response.status_code >= 400:
                        logger.info(
                            f"Fetch of {current} returned {response.status_code}"
                        )
                        return None

                    content_type = response.headers.get("content-type", "")
                    if "html" not in content_type.lower():
                        logger.info(
                            f"Not fetching non-HTML {content_type!r} at {current}"
                        )
                        return None

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= MAX_BYTES:
                            break

                    html = bytes(body[:MAX_BYTES]).decode(
                        response.encoding or "utf-8", errors="replace"
                    )
                    return parse_page(html, url=current)

            logger.info(f"Too many redirects fetching {url}")
            return None
    except (httpx.HTTPError, LookupError, ValueError) as exc:
        # LookupError: an unknown charset in the Content-Type header.
        logger.info(f"Could not fetch {url}: {exc!r}")
        return None
