# -*- coding: utf-8 -*-
"""
This module contains functions for handling YouTube URLs and extracting metadata.

It provides functions to detect YouTube URLs, extract video IDs, and get video information
using the YouTube oEmbed API to avoid bot detection issues.

Functions:
    is_youtube_url(url: str) -> bool: Check if a URL is a YouTube URL.
    extract_youtube_video_id(url: str) -> str: Extract the video ID from a YouTube URL.
    get_youtube_metadata(video_id: str) -> Dict[str, str]: Get video metadata from YouTube.
    get_youtube_summary(url: str, sentence_length: int) -> Dict[str, str]: Get AI-generated summary.
    get_youtube_title(url: str) -> str: Get a clean title for a YouTube video.

Author:
    Mike Ryan
    MIT Licensed
"""
import re
from typing import Dict
from urllib.parse import urlparse

from httpx import AsyncClient
from loguru import logger

from src.settings import settings

from .ai import get_model_temperature

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

client = (
    AsyncOpenAI(api_key=settings.openai_key.get_secret_value()) if AsyncOpenAI else None
)

temperature = 0.2


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


async def get_youtube_metadata(video_id: str) -> Dict[str, str]:
    """
    Get YouTube video metadata using the oEmbed API (no API key required).

    Args:
        video_id (str): The YouTube video ID.

    Returns:
        Dict[str, str]: Dictionary containing title, description, and other metadata.
    """
    try:
        async with AsyncClient() as client:
            # Using YouTube's oEmbed API which doesn't require authentication
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = await client.get(oembed_url)

            if response.status_code == 200:
                data = response.json()
                return {
                    "title": data.get("title", ""),
                    "author_name": data.get("author_name", ""),
                    "description": f"YouTube video by {data.get('author_name', 'Unknown')}: {data.get('title', 'No title available')}",
                    "thumbnail_url": data.get("thumbnail_url", ""),
                }
            else:
                logger.warning(
                    f"Failed to get YouTube metadata for video {video_id}: {response.status_code}"
                )
                return {
                    "title": "YouTube Video",
                    "description": "YouTube video content",
                    "author_name": "",
                    "thumbnail_url": "",
                }

    except Exception as e:
        logger.error(f"Error getting YouTube metadata for video {video_id}: {e}")
        return {
            "title": "YouTube Video",
            "description": "YouTube video content",
            "author_name": "",
            "thumbnail_url": "",
        }


def strip_quotation_marks(text: str) -> str:
    """
    Removes various types of quotation marks from the given text.

    Args:
        text (str): The input string from which quotation marks will be removed.

    Returns:
        str: The input string with all quotation marks removed.
    """
    return (
        text.replace('"', "")
        .replace("'", "")
        .replace(
            """, "")
        .replace(""",
            "",
        )
        .replace("'", "")
        .replace("'", "")
    )


async def get_youtube_summary(url: str, sentence_length: int = 2) -> Dict[str, str]:
    """
    Get a summary for a YouTube video using AI with metadata from oEmbed.

    Args:
        url (str): The YouTube URL.
        sentence_length (int, optional): The length of the summary in sentences. Defaults to 2.

    Returns:
        Dict[str, str]: A dictionary containing the summary.
    """
    logger.info("Starting get_youtube_summary function")

    video_id = extract_youtube_video_id(url)
    if not video_id:
        logger.error(f"Could not extract video ID from URL: {url}")
        return {
            "summary": "YouTube video content - unable to extract video information"
        }

    metadata = await get_youtube_metadata(video_id)

    # Create a prompt using the available metadata
    content = f"YouTube video titled '{metadata['title']}' by {metadata['author_name']}"

    prompt = f"Please create a {sentence_length} sentence summary for a YouTube video with the title '{metadata['title']}' by {metadata['author_name']}. Create an informative description about what this video likely contains based on the title and creator."

    try:
        chat_completion = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {"role": "user", "content": content},
            ],
            temperature=get_model_temperature(settings.openai_model, temperature),
        )

        response_content = chat_completion.choices[0].message.content
        logger.debug(f"YouTube summary content: {response_content}")

        return {"summary": response_content}

    except Exception as e:
        logger.error(f"Error generating YouTube summary: {e}")
        # Fallback to metadata-based summary
        fallback_summary = (
            f"YouTube video '{metadata['title']}' by {metadata['author_name']}"
        )
        return {"summary": fallback_summary}


async def get_youtube_title(url: str) -> str:
    """
    Get a clean title for a YouTube video.

    Args:
        url (str): The YouTube URL.

    Returns:
        str: The cleaned video title.
    """
    logger.info("Starting get_youtube_title function")

    video_id = extract_youtube_video_id(url)
    if not video_id:
        logger.error(f"Could not extract video ID from URL: {url}")
        return "YouTube Video"

    metadata = await get_youtube_metadata(video_id)

    # Clean up the title - remove common YouTube suffixes and prefixes
    title = metadata["title"]
    if title:
        # Remove common patterns like " - YouTube", " | YouTube", etc.
        title = re.sub(r"\s*[-|]\s*YouTube\s*$", "", title, flags=re.IGNORECASE)
        title = strip_quotation_marks(title)
        return title[:100]  # Limit length

    return "YouTube Video"
