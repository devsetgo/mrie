# -*- coding: utf-8 -*-
"""
This module contains functions for handling YouTube URLs and extracting metadata.

It provides functions to detect YouTube URLs, extract video IDs, and get video information
using the YouTube oEmbed API to avoid bot detection issues.

Functions:
    is_youtube_url(url: str) -> bool: Check if a URL is a YouTube URL.
    extract_youtube_video_id(url: str) -> str: Extract the video ID from a YouTube URL.
    get_youtube_metadata(video_id: str) -> Dict[str, str]: Get video metadata from YouTube.

Author:
    Mike Ryan
    MIT Licensed
"""

import re
import time
from typing import Dict
from urllib.parse import urlparse

from httpx import AsyncClient
from loguru import logger

DEFAULT_YOUTUBE_TITLE = "YouTube Video"


def is_youtube_url(url: str) -> bool:
    """
    Check if the URL is a YouTube URL.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if it's a YouTube URL, False otherwise.
    """
    youtube_domains = ["youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"]
    parsed_url = urlparse(url)
    return parsed_url.netloc.lower() in youtube_domains


def extract_youtube_video_id(url: str) -> str:
    """
    Extract the video ID from a YouTube URL.

    Args:
        url (str): The YouTube URL.

    Returns:
        str: The video ID or empty string if not found.
    """
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",  # Standard format
        r"(?:embed\/)([0-9A-Za-z_-]{11})",  # Embed format
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",  # Short format
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return ""


# A short in-process cache, so re-analyzing the same video (create, then an
# immediate "update") doesn't repeat the oEmbed HTTP round trip. Only
# successful lookups are cached, so a transient oEmbed failure doesn't
# get "stuck" returning the fallback for the full TTL.
_METADATA_CACHE: dict[str, tuple[Dict[str, str], float]] = {}
_METADATA_CACHE_TTL_SECONDS = 300


async def get_youtube_metadata(video_id: str) -> Dict[str, str]:
    """
    Get YouTube video metadata using the oEmbed API (no API key required).

    Args:
        video_id (str): The YouTube video ID.

    Returns:
        Dict[str, str]: Dictionary containing title, description, and other metadata.
    """
    cached = _METADATA_CACHE.get(video_id)
    if cached is not None:
        metadata, cached_at = cached
        if time.monotonic() - cached_at < _METADATA_CACHE_TTL_SECONDS:
            return metadata

    try:
        async with AsyncClient() as client:
            # Using YouTube's oEmbed API which doesn't require authentication
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = await client.get(oembed_url)

            if response.status_code == 200:
                data = response.json()
                metadata = {
                    "title": data.get("title", ""),
                    "author_name": data.get("author_name", ""),
                    "description": f"YouTube video by {data.get('author_name', 'Unknown')}: {data.get('title', 'No title available')}",
                    "thumbnail_url": data.get("thumbnail_url", ""),
                }
                _METADATA_CACHE[video_id] = (metadata, time.monotonic())
                return metadata
            else:
                logger.warning(
                    f"Failed to get YouTube metadata for video {video_id}: {response.status_code}"
                )
                return {
                    "title": DEFAULT_YOUTUBE_TITLE,
                    "description": "YouTube video content",
                    "author_name": "",
                    "thumbnail_url": "",
                }

    except Exception as e:
        logger.error(f"Error getting YouTube metadata for video {video_id}: {e}")
        return {
            "title": DEFAULT_YOUTUBE_TITLE,
            "description": "YouTube video content",
            "author_name": "",
            "thumbnail_url": "",
        }
