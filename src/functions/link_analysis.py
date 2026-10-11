# -*- coding: utf-8 -*-
"""
Title + summary for a bookmarked link, from the page itself.

analyze_link() gathers what the page actually says - YouTube's oEmbed
metadata for videos, otherwise the fetched page's title/description/text
(page_fetch.fetch_page) - and makes one AI call for both fields
(ai.get_link_analysis). If the page can't be fetched, the model gets the
URL alone and is told not to invent details.

process_link_background() is what create/re-analyze/CSV import schedule: it
runs after the response has gone out, so saving a link never waits on a
page fetch plus an OpenAI round trip. The row holds the WEBLINK_PROCESSING
placeholder until it finishes (the view page auto-refreshes meanwhile).

Author:
    Mike Ryan
    MIT Licensed
"""

from typing import Dict, Optional

from loguru import logger
from sqlalchemy import Select, update

from ..db_tables import WebLinks, compute_weblink_ai_fix
from ..resources import db_ops
from . import ai, link_preview
from .db_guards import is_db_error, safe_record
from .page_fetch import PageContent, fetch_page
from .youtube_helper import (
    DEFAULT_YOUTUBE_TITLE,
    extract_youtube_video_id,
    get_youtube_metadata,
    is_youtube_url,
)


async def _page_content(url: str) -> Optional[PageContent]:
    """
    The page text to analyze, or None to analyze from the URL alone.

    YouTube is read through oEmbed; everything else through fetch_page(),
    which applies the SSRF guard at every redirect hop.
    """
    if is_youtube_url(url):
        # youtube.com serves bot/consent pages to plain HTTP clients; oEmbed
        # is the reliable source for a video's title and channel.
        video_id = extract_youtube_video_id(url)
        if video_id:
            metadata = await get_youtube_metadata(video_id)
            if metadata["title"] != DEFAULT_YOUTUBE_TITLE:
                return PageContent(
                    url=url,
                    title=metadata["title"],
                    description=metadata["description"],
                )
        return None
    return await fetch_page(url)


async def analyze_link(url: str) -> Dict[str, str]:
    """
    Returns {"title", "summary"} for the link. Raises RuntimeError when AI
    is unavailable (see ai.require_client), like every other AI entry point.
    """
    page = await _page_content(url)
    if page is None or page.is_empty:
        logger.info(f"No page content for {url}; analyzing from the URL alone")
    return await ai.get_link_analysis(url, page)


async def process_link_background(
    pkid: str, url: str, capture_screenshot: bool = True
) -> None:
    """
    Fills in a saved link's title/summary, then (optionally) its screenshot.
    Never raises - it runs as a background task, where an exception would
    only become a stack trace in the log.

    On failure the row keeps whatever it had (the placeholder for a new
    link, the previous values for a re-analysis), and ai_fix stays set
    while either field is still the placeholder.
    """
    try:
        analysis = await analyze_link(url)
    except RuntimeError as exc:
        logger.warning(f"Link analysis unavailable for {pkid}: {exc}")
        analysis = None
    except Exception:
        logger.exception(f"Link analysis failed for {pkid}")
        analysis = None

    if analysis is not None:
        record = safe_record(
            await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
        )
        if record is None:
            logger.warning(f"Link {pkid} was deleted before its analysis finished")
            return
        result = await db_ops.execute_one(
            update(WebLinks)
            .where(WebLinks.pkid == pkid)
            .values(
                title=analysis["title"],
                summary=analysis["summary"],
                ai_fix=compute_weblink_ai_fix(
                    image_preview_data=record.image_preview_data,
                    title=analysis["title"],
                    summary=analysis["summary"],
                ),
            )
        )
        if is_db_error(result):
            logger.error(f"Error saving analysis for link {pkid}: {result}")

    if capture_screenshot:
        try:
            await link_preview.capture_full_page_screenshot(url=url, pkid=pkid)
        except (RuntimeError, ValueError) as exc:
            # RuntimeError: selenium isn't installed by default
            # (requirements/optional.txt). ValueError: unsafe URL, refused.
            logger.info(f"Screenshot skipped for {pkid}: {exc}")
        except Exception:
            logger.exception(f"Screenshot failed for {pkid}")
