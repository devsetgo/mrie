# -*- coding: utf-8 -*-
"""
Safe rendering of note bodies for the Jinja templates.

A note's body is free-form HTML or markdown that the author typed or
imported (CSV exports from elsewhere), and the view pages used to emit it
with `|safe`, so a stray `<script>` or `<img onerror=...>` ran in the
logged-in admin's origin - which can read the whole journal. These two
filters are the only sanctioned way to put a note body in a page.

Both fail closed: if nh3 isn't installed the text is HTML-escaped rather
than passed through (the optional-dependency pattern in CLAUDE.md - the app
must still boot and serve pages).

Author:
    Mike Ryan
    MIT Licensed
"""

import html

from markupsafe import Markup

try:
    import nh3
except ImportError:
    nh3 = None

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

_markdown = None


def sanitize_html(value) -> Markup:
    """
    Returns `value` as HTML with scripts, event handlers, `javascript:`
    URLs, iframes, forms and inline styles removed. nh3's default
    allowlist keeps normal formatting (paragraphs, lists, tables, links,
    images, emphasis) and adds rel="noopener noreferrer" to links.
    """
    text = "" if value is None else str(value)
    if nh3 is None:
        return Markup(html.escape(text))
    return Markup(nh3.clean(text))


def render_markdown(value) -> Markup:
    """
    Renders markdown to sanitized HTML on the server.

    Raw HTML inside the markdown is escaped (`html=False`) rather than
    rendered, and markdown-it refuses `javascript:`/`data:` link targets;
    the result then goes through sanitize_html() as a second layer.
    """
    global _markdown
    text = "" if value is None else str(value)
    if MarkdownIt is None:
        return Markup(f"<pre>{html.escape(text)}</pre>")
    if _markdown is None:
        _markdown = (
            MarkdownIt("commonmark", {"html": False})
            .enable("table")
            .enable("strikethrough")
        )
    return sanitize_html(_markdown.render(text))
