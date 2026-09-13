# -*- coding: utf-8 -*-
"""
This module contains functions for interacting with the OpenAI API.

It uses the AsyncOpenAI client to make asynchronous requests to the API. The module includes functions for getting tags, summary, mood analysis, and mood from a given content.

Functions:
    get_analysis(content: str, mood_process: str = None) -> dict: Gets the tags, summary, mood analysis, and mood from the given content.

Author:
    Mike Ryan
    MIT Licensed
"""

import ast
import json
import re
from functools import lru_cache
from typing import Dict, List, Optional

from httpx import AsyncClient
from loguru import logger

from src.settings import settings

from ._names import names

try:
    from nameparser import HumanName
except ImportError:
    HumanName = None

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

client = (
    AsyncOpenAI(
        # This is the default and can be omitted
        api_key=settings.openai_key.get_secret_value(),
    )
    if AsyncOpenAI
    else None
)

mood_analysis = [mood[0] for mood in settings.mood_analysis_weights]

timeout = 10
temperature = 0.2


def get_model_temperature(
    model: str, requested_temperature: Optional[float] = None
) -> float:
    """
    Get the appropriate temperature for the given model.

    Args:
        model (str): The OpenAI model name
        requested_temperature (Optional[float], optional): The requested temperature

    Returns:
        float: The temperature to use for the model
    """
    # gpt-5-nano only supports temperature = 1 (default)
    if model == "gpt-5-nano":
        return 1.0

    # For other models, use requested temperature or default
    return requested_temperature if requested_temperature is not None else temperature


async def get_analysis(content: str, mood_process: str = None) -> dict:
    """
    Gets tags, summary, mood_analysis, and mood in a single API call.

    Args:
        content (str): The journal content to analyze.
        mood_process (str, optional): Kept for backward compatibility; no longer used.

    Returns:
        dict with keys: tags ({"tags": [...]}), summary (str),
        mood_analysis (str), mood ({"mood": str}).
        On failure returns safe defaults plus _ai_fix=True sentinel.
    """
    logger.info("Starting get_analysis (single call)")
    moods_list = [m[0] for m in settings.mood_analysis_weights]
    model = settings.openai_model

    system_prompt = (
        "Analyze this journal entry. Respond ONLY with valid JSON in exactly this shape:\n"
        '{"tags": ["word1"], "summary": "...", "mood_analysis": "...", "mood": "..."}\n\n'
        "Rules:\n"
        "- tags: 1-3 single lowercase words, no person names\n"
        "- summary: 3-6 words, title-style, no person names\n"
        f"- mood_analysis: one word from {moods_list}\n"
        '- mood: one of ["positive", "negative", "neutral"]\n'
    )

    try:
        chat_completion = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=get_model_temperature(model),
            response_format={"type": "json_object"},
        )
        raw = chat_completion.choices[0].message.content
        logger.debug(f"get_analysis raw response: {raw}")
        parsed = json.loads(raw)

        raw_tags = parsed.get("tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = []
        raw_tags = ["".join(re.findall("[a-zA-Z]+", str(t))) for t in raw_tags if t]
        tags_dict = tag_check({"tags": raw_tags})

        summary = str(parsed.get("summary", "")).strip()

        mood_analysis_val = (parsed.get("mood_analysis") or "").strip().lower()
        if mood_analysis_val not in moods_list:
            logger.warning(
                f"get_analysis: unexpected mood_analysis '{mood_analysis_val}', defaulting to 'neutral'"
            )
            mood_analysis_val = "neutral"

        mood_val = (parsed.get("mood") or "").strip().lower()
        if mood_val not in ("positive", "negative", "neutral"):
            logger.warning(
                f"get_analysis: unexpected mood '{mood_val}', defaulting to 'neutral'"
            )
            mood_val = "neutral"

        data = {
            "tags": tags_dict,  # {"tags": [...]} — callers use analysis["tags"]["tags"]
            "summary": summary,
            "mood_analysis": mood_analysis_val,
            "mood": {
                "mood": mood_val
            },  # {"mood": "..."} — note_import uses analysis["mood"]["mood"]
        }
        logger.info("get_analysis completed (single call)")
        logger.debug(f"analysis: {data}")
        return data

    except (json.JSONDecodeError, KeyError, AttributeError) as exc:
        logger.error(f"get_analysis JSON parse failure: {exc}")
        return {
            "tags": {"tags": []},
            "summary": "",
            "mood_analysis": "neutral",
            "mood": {"mood": "neutral"},
            "_ai_fix": True,
        }


async def get_blog_post_analysis(
    content: str, sentence_length: int = 3, keyword_limit: int = 3
) -> dict:
    """
    Gets tags and summary for a blog post in a single API call.

    Returns:
        dict: {"tags": [...], "summary": "..."} — tags is a flat list,
        matching blog_posts.py's direct use of analysis["tags"].
    """
    logger.info("Starting get_blog_post_analysis (single call)")
    model = settings.openai_model

    system_prompt = (
        f"Analyze this blog post. Respond ONLY with valid JSON in exactly this shape:\n"
        f'{{"tags": ["word1"], "summary": "..."}}\n\n'
        f"Rules:\n"
        f"- tags: 1-{keyword_limit} single lowercase words, no person names\n"
        f"- summary: {sentence_length}-sentence summary, no person names\n"
    )

    try:
        chat_completion = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=get_model_temperature(model),
            response_format={"type": "json_object"},
        )
        raw = chat_completion.choices[0].message.content
        logger.debug(f"get_blog_post_analysis raw: {raw}")
        parsed = json.loads(raw)

        raw_tags = parsed.get("tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = []
        raw_tags = ["".join(re.findall("[a-zA-Z]+", str(t))) for t in raw_tags if t]
        tags_dict = tag_check({"tags": raw_tags})

        return {
            "tags": tags_dict[
                "tags"
            ],  # flat list — blog_posts.py uses analysis["tags"] directly
            "summary": str(parsed.get("summary", "")).strip(),
        }

    except (json.JSONDecodeError, KeyError, AttributeError) as exc:
        logger.error(f"get_blog_post_analysis failure: {exc}")
        return {"tags": [], "summary": ""}


async def get_tags(
    content: str, temperature: float = temperature, keyword_limit: int = 3
) -> dict:
    """
    Gets a list of keywords from the given content.

    Args:
        content (str): The content to analyze.
        temperature (float, optional): The temperature to use for the OpenAI API. Defaults to temperature.
        keyword_limit (int, optional): The maximum number of keywords to return. Defaults to 3.

    Returns:
        dict: A dictionary containing the keywords.
    """
    logger.info("Starting get_tags function")

    # Create the prompt for the OpenAI API
    prompt = f"For the following text create a python style list between 1 to {keyword_limit} 'single word' keywords to be stored as a python list and cannot be a persons name: {content}"

    # Send the prompt to the OpenAI API
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

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"tag response: {response_content}")

    # Use a regular expression to extract a list from the response
    match = re.search(r"\[.*\]", response_content)
    if match:
        response_content = match.group(0)

    else:
        logger.error(
            f"Error: Unable to extract a list from response_content: {response_content}"
        )
        response_content = "[]"

    logger.debug(response_content)
    # Convert string representation of list to list
    try:
        response_content = ast.literal_eval(response_content)
    except SyntaxError:
        logger.error(f"Error: Unable to parse response_content: {response_content}")
        response_content = []

    # Remove any numbers or symbols from the items in the list
    response_content = [
        "".join(re.findall("[a-zA-Z]+", item)) for item in response_content
    ]

    # Store the keywords in a dictionary
    logger.debug(f"tag content {response_content}")
    response_dict = {"tags": response_content}
    logger.info("Finished get_tags function")
    resp = tag_check(response_dict)
    logger.debug(response_dict)
    return resp


# Convert names list to set for fast lookups (case insensitive)
COMPREHENSIVE_NAMES = {name.lower() for name in names}


@lru_cache(maxsize=128)
def name_check(name: str) -> bool:
    """
    Check if the text is a person's name using a comprehensive names database.
    Prioritizes family names and includes technical word exceptions.

    Args:
        name: The text to be checked.

    Returns:
        True if the text is recognized as a person's name, False otherwise.
    """
    # Convert to lowercase for comparison
    name_lower = name.lower().strip()

    # Technical/common words that should never be filtered as names
    # These take precedence over the names database
    technical_words = {
        "technology",
        "programming",
        "science",
        "computer",
        "software",
        "data",
        "code",
        "algorithm",
        "database",
        "network",
        "system",
        "application",
        "development",
        "framework",
        "library",
        "api",
        "web",
        "mobile",
        "cloud",
        "security",
        "testing",
        "debugging",
        "optimization",
        "analysis",
        "design",
        "architecture",
        "implementation",
        "interface",
        "protocol",
        "server",
        "client",
        "backend",
        "frontend",
        "deployment",
        "repository",
        "version",
        "configuration",
        "documentation",
        "performance",
        "scalability",
    }

    # Check if it's a technical word first (should never be filtered)
    if name_lower in technical_words:
        return False

    # Check against the comprehensive names database
    if name_lower in COMPREHENSIVE_NAMES:
        return True

    # Use nameparser for compound names (first + last name combinations),
    # when installed - see requirements/prd.txt
    if HumanName is not None:
        parsed_name = HumanName(name)
        if parsed_name.first and parsed_name.last:
            return True

        # Check for title indicators (Mr., Mrs., Dr., etc.)
        if parsed_name.title:
            return True

    return False


def tag_check(tags: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Filter out tags that are recognized as person names by the spaCy model.

    Args:
        tags: A dictionary with a key "tags" containing a list of strings to be checked.

    Returns:
        A dictionary with the key "tags" containing a filtered list of strings
        where any recognized person names have been removed.
    """
    # Load the multi-language model

    tag_list = tags["tags"]
    logger.debug(tag_list)
    filtered_tags = [tag for tag in tag_list if not name_check(tag)]
    logger.debug(filtered_tags)
    return {"tags": filtered_tags}


async def get_summary(
    content: str, temperature: float = temperature, sentence_length: int = 1
) -> dict:
    """
    Gets a summary of the given content.

    Args:
        content (str): The content to summarize.
        temperature (float, optional): The temperature to use for the OpenAI API. Defaults to temperature.
        sentence_length (int, optional): The length of the summary in sentences. Defaults to 1.

    Returns:
        dict: A dictionary containing the summary.
    """
    logger.info("Starting get_summary function")

    # Create the prompt for the OpenAI API
    prompt = f"Create a very brief {sentence_length}-sentence title-style summary. Use only 3-6 words maximum. Focus on the main topic only, avoid detailed explanations. No person names allowed."

    # Send the prompt to the OpenAI API
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

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"summary content: {response_content}")

    # Store the summary in a dictionary
    response_dict = response_content
    logger.info("Finished get_summary function")

    return response_dict


async def get_mood_analysis(content: str, temperature: float = temperature) -> dict:
    """
    Analyzes the mood of the given content.

    Args:
        content (str): The content to analyze.
        temperature (float, optional): The temperature to use for the OpenAI API. Defaults to temperature.

    Returns:
        dict: A dictionary containing the mood analysis.
    """
    logger.info("Starting get_mood_analysis function")
    moods = [mood[0] for mood in settings.mood_analysis_weights]
    # Create the prompt for the OpenAI API
    prompt = f"For the following text provide a single expressive word response that expresses the general mood of the content from these options {moods}. It will be stored in a python variable with a max character length of 25."

    # Send the prompt to the OpenAI API
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

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"mood analysis content: {response_content}")

    # Store the mood analysis in a dictionary
    response_dict = {"mood_analysis": response_content}
    logger.info("Finished get_mood_analysis function")

    return response_dict


async def get_mood(content: str, temperature: float = temperature) -> dict:
    """
    Determines the mood of the given content.

    Args:
        content (str): The content to analyze.
        temperature (float, optional): The temperature to use for the OpenAI API. Defaults to temperature.

    Returns:
        dict: A dictionary containing the mood.
    """
    # Define the possible moods
    moods: list = ["positive", "negative", "neutral"]
    logger.info("Starting get_mood function")

    # Create the prompt for the OpenAI API
    prompt = f"Please determine the mood of the following text using only one of these moods {moods} for the content. It will be stored in a python variable with a max character length of 25."

    # Send the prompt to the OpenAI API
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

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"mood content: {response_content}")

    # Store the mood in a dictionary
    response_dict = {"mood": response_content}

    logger.info("Finished get_mood function")

    return response_dict


async def analyze_post(content: str, temperature: float = temperature) -> dict:
    """Analyzes a post. Delegates to get_analysis() for a single API call."""
    logger.info("Starting analyze_post function")
    data = await get_analysis(content=content)
    logger.info("Finished analyze_post function")
    return data


async def get_url_summary(
    url: str, temperature: float = temperature, sentence_length: int = 1
) -> Dict[str, str]:
    """
    Gets a summary of the given URL using the OpenAI API.

    Args:
        url (str): The URL to summarize.
        temperature (float, optional): The temperature setting for the OpenAI API. Defaults to the value of `temperature`.
        sentence_length (int, optional): The length of the summary in sentences. Defaults to 2.

    Returns:
        Dict[str, str]: A dictionary containing the summary.
    """
    logger.info("Starting get_url_summary function")

    # Check if it's a YouTube URL and handle it specially
    from ..functions.youtube_helper import get_youtube_summary, is_youtube_url

    if is_youtube_url(url):
        logger.info("Detected YouTube URL, using YouTube-specific handler")
        return await get_youtube_summary(url, sentence_length)

    # Create the prompt for the OpenAI API
    prompt = f"Create a very brief {sentence_length}-word title for this URL. Use only 3-6 words maximum. Focus on the main topic only."

    # Send the prompt to the OpenAI API
    chat_completion = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": prompt,
            },
            {"role": "user", "content": url},
        ],
        temperature=get_model_temperature(settings.openai_model, temperature),
    )

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"summary content: {response_content}")

    # Store the summary in a dictionary
    response_dict = {"summary": response_content}
    logger.info("Finished get_url_summary function")

    return response_dict


async def get_url_title(
    url: str, temperature: float = 0.7, sentence_length: int = 1
) -> str:
    """
    Generates a new title from the website's full title HTML tag using the OpenAI API.

    Args:
        url (str): The URL of the website to generate the title from.
        temperature (float, optional): The temperature setting for the OpenAI API. Defaults to 0.7.
        sentence_length (int, optional): The length of the sentence to generate. Defaults to 1.

    Returns:
        str: The generated title after removing quotation marks.
    """
    logger.info("Starting get_url_title function")

    # Check if it's a YouTube URL and handle it specially
    from ..functions.youtube_helper import get_youtube_title, is_youtube_url

    if is_youtube_url(url):
        logger.info("Detected YouTube URL, using YouTube-specific handler")
        return await get_youtube_title(url)

    # Create the prompt for the OpenAI API
    # prompt = "Create a new title from the websites full title html tag and format as 'Full Title from Website Name'. If not possible provide a title that is a simple single sentence in length."
    prompt = f"Create a title for this websites. It should be only {sentence_length} sentence in length and cannot contain any persons name."
    # Send the prompt to the OpenAI API
    chat_completion = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": prompt,
            },
            {"role": "user", "content": url},
        ],
        temperature=get_model_temperature(settings.openai_model, temperature),
    )

    # Extract the content from the response
    response_content = chat_completion.choices[0].message.content
    logger.debug(f"title content: {response_content}")

    # Store the summary in a dictionary
    response_dict = response_content
    logger.info("Finished get_url_title function")
    text = strip_quotation_marks(response_dict)
    return text


async def get_html_title(url: str) -> str:
    """
    Get the title of a webpage from the HTML title tag.

    Args:
        url (str): The URL of the webpage.

    Returns:
        str: The title of the webpage.
    """
    async with AsyncClient() as client:
        response = await client.get(url)
        html = response.text
        # Updated regular expression to match <title> tag with possible attributes
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
        if title:
            return title.group(1)
        return "Not Found"


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
        .replace("“", "")
        .replace("”", "")
        .replace("‘", "")
        .replace("’", "")
    )
