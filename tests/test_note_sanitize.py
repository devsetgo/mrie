# -*- coding: utf-8 -*-
"""
Note bodies are free-form HTML/markdown and must never reach a page
unsanitized (src/functions/sanitize.py). Covers the filters directly and
the three templates that emit a note body.
"""

from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from src.functions import sanitize
from src.resources import templates

HOSTILE = (
    '<p onclick="steal()">Hello <strong>world</strong></p>'
    "<script>steal()</script>"
    "<img src=x onerror=steal()>"
    '<a href="javascript:steal()">click</a>'
    '<iframe src="https://evil.example"></iframe>'
    "<style>body{display:none}</style>"
)


class _Elements(HTMLParser):
    """Collects what a browser would actually act on: tags and attributes."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)


def _assert_neutral(html: str):
    """
    No executable element or attribute. Escaped *text* such as
    "&lt;script&gt;" is fine - it is displayed, not run - so this parses the
    markup instead of searching for substrings.
    """
    parsed = _Elements()
    parsed.feed(html)
    assert not {"script", "iframe", "style", "object", "embed"} & set(parsed.tags)
    for name, value in parsed.attrs:
        assert not name.startswith("on"), name
        assert not (value or "").strip().lower().startswith("javascript:"), value


def test_sanitize_html_removes_scripts_handlers_and_unsafe_urls():
    cleaned = str(sanitize.sanitize_html(HOSTILE))
    _assert_neutral(cleaned)
    assert "<strong>world</strong>" in cleaned


def test_sanitize_html_keeps_ordinary_formatting():
    body = (
        "<h2>Day</h2><ul><li>one</li></ul>"
        '<p><a href="https://example.com">link</a> <em>em</em></p>'
    )
    cleaned = str(sanitize.sanitize_html(body))
    for fragment in (
        "<h2>Day</h2>",
        "<li>one</li>",
        "<em>em</em>",
        'href="https://example.com"',
    ):
        assert fragment in cleaned


def test_sanitize_html_handles_none():
    assert str(sanitize.sanitize_html(None)) == ""


def test_render_markdown_escapes_raw_html_and_blocks_script_links():
    rendered = str(
        sanitize.render_markdown(
            "# Title\n\n**bold** [bad](javascript:steal()) <script>steal()</script>\n"
        )
    )
    _assert_neutral(rendered)
    assert "<h1>Title</h1>" in rendered
    assert "<strong>bold</strong>" in rendered


def test_render_markdown_supports_tables():
    rendered = str(sanitize.render_markdown("|a|b|\n|-|-|\n|1|2|\n"))
    assert "<table>" in rendered and "<td>1</td>" in rendered


def test_filters_fail_closed_without_the_optional_packages(monkeypatch):
    monkeypatch.setattr(sanitize, "nh3", None)
    monkeypatch.setattr(sanitize, "MarkdownIt", None)
    assert str(sanitize.sanitize_html("<script>x</script>")) == (
        "&lt;script&gt;x&lt;/script&gt;"
    )
    assert "&lt;script&gt;" in str(sanitize.render_markdown("<script>x</script>"))


@pytest.mark.parametrize(
    "template", ["notes/note_view_html.html", "notes/note_view_md.html"]
)
def test_note_view_templates_never_emit_a_hostile_body(template):
    page = templates.env.get_template(template).render(
        note=SimpleNamespace(note=HOSTILE)
    )
    _assert_neutral(page)
    # The markdown view no longer ships a CDN script or a hidden textarea.
    assert "<textarea" not in page and "marked" not in page


def test_delete_page_sanitizes_the_note_body():
    source = templates.env.loader.get_source(templates.env, "notes/delete.html")[0]
    assert "note.note | sanitize_html" in source
    assert "note.note|safe" not in source
