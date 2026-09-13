# -*- coding: utf-8 -*-
"""
pagination.py

Shared helper for building a paginated list's prev/next page URLs.
notes.py and web_links.py each build a "?page=N&<carried-over filters>" URL
the same way; a plain f-string join (as web_links.py used to do) doesn't
URL-encode filter values, so a search term containing "&", "=", or a space
breaks the resulting URL.

Author:
    Mike Ryan
    MIT Licensed
"""

from urllib.parse import urlencode


def build_page_url(base_path: str, page: int, params) -> str:
    """
    Build a "<base_path>?page=<page>&<urlencoded carried-over params>" URL.

    Args:
        base_path (str): The route path, e.g. "/notes/pagination".
        page (int): The page number to link to.
        params: An iterable of (key, value) pairs to carry over - falsy
            values are dropped, matching how callers already treat "no
            filter set". A plain dict works too; pass a list of pairs
            instead if a key needs to repeat (e.g. multiple "tags" values).

    Returns:
        str: The built URL, with values properly URL-encoded.
    """
    if isinstance(params, dict):
        params = params.items()
    pairs = [("page", page)] + [(key, value) for key, value in params if value]
    return f"{base_path}?{urlencode(pairs)}"
