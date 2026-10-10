# -*- coding: utf-8 -*-
"""Link status checks and Selenium screenshot capture for weblinks."""

import time

import anyio
import httpx
from loguru import logger
from sqlalchemy import Select, update

from ..db_tables import WebLinks, compute_weblink_ai_fix
from ..functions.db_guards import safe_record as _safe_record
from ..resources import db_ops
from .page_fetch import is_safe_public_url

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    webdriver = Options = Service = ChromeDriverManager = None

client = httpx.AsyncClient()


async def url_status(url: str) -> bool:
    """
    Checks the status of the given URL.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if the URL is not reachable (i.e., status code is not 2xx or 3xx), False otherwise.
    """

    # Same SSRF guard as page fetching and screenshots - this runs on every
    # view of a link. No redirects are followed (httpx default), so the one
    # checked hop is the only one requested.
    if not await anyio.to_thread.run_sync(is_safe_public_url, url):
        return False

    try:
        response = await client.get(url)
        if response.status_code < 400:
            return True
        return False
    except Exception:
        return True


async def save_preview_image(pkid: str, image: bytes):
    try:
        record = _safe_record(
            await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
        )
        if record is None:
            logger.error(f"Error saving preview image: no weblink found with ID {pkid}")
            return

        # This is the update that normally clears ai_fix once all three
        # pieces (title, summary, screenshot) exist - see
        # compute_weblink_ai_fix in db_tables.py.
        await db_ops.execute_one(
            update(WebLinks)
            .where(WebLinks.pkid == pkid)
            .values(
                image_preview_data=image,
                ai_fix=compute_weblink_ai_fix(
                    image_preview_data=image,
                    title=record.title,
                    summary=record.summary,
                ),
            )
        )
    except Exception as e:
        error: str = f"Error saving preview image: {e}"
        logger.error(error)


def _capture_full_page_screenshot_sync(url: str) -> bytes:
    """
    Synchronous Selenium body of capture_full_page_screenshot(). Runs in a
    worker thread (see below) - driver.get()/find_element()/time.sleep() are
    all blocking calls that would otherwise stall the event loop for the
    duration of the page load.
    """
    from .youtube_helper import is_youtube_url

    if not is_safe_public_url(url):
        raise ValueError(f"Refusing to capture screenshot for unsafe URL: {url!r}")

    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    chrome_options.binary_location = "/usr/bin/google-chrome"  # Path to Chrome binary

    # For YouTube URLs, add additional options to avoid bot detection
    if is_youtube_url(url):
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

    # Initialize the Chrome driver
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=chrome_options
    )

    try:
        # For YouTube, execute script to remove automation indicators
        if is_youtube_url(url):
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

        # Navigate to the URL
        driver.get(url)

        # For YouTube, wait a bit longer and handle cookie acceptance
        if is_youtube_url(url):
            time.sleep(3)  # Wait for page to load

            # Try to accept cookies if the banner appears
            try:
                cookie_button = driver.find_element(
                    "xpath",
                    "//button[contains(text(), 'Accept') or contains(text(), 'I agree')]",
                )
                cookie_button.click()
                time.sleep(1)
            except Exception:
                pass  # Cookie banner might not appear

        # Set the window size to the full page
        total_height = driver.execute_script(
            "return Math.max(document.body.scrollHeight, document.body.offsetHeight, "
            "document.documentElement.clientHeight, document.documentElement.scrollHeight, "
            "document.documentElement.offsetHeight);"
        )
        driver.set_window_size(1920, total_height)

        # Capture the screenshot
        return driver.get_screenshot_as_png()
    finally:
        driver.quit()


async def capture_full_page_screenshot(url: str, pkid: str) -> bytes:
    """
    Captures a full-page screenshot of the given URL and returns the image data as bytes.
    For YouTube URLs, captures the video page with special handling.

    Args:
        url (str): The URL of the webpage to capture.
        pkid (str): The primary key ID for the weblink.

    Returns:
        bytes: The image data of the screenshot.
    """
    if webdriver is None:
        raise RuntimeError(
            "selenium/webdriver-manager not installed - screenshot capture disabled"
        )

    screenshot_as_bytes = await anyio.to_thread.run_sync(
        _capture_full_page_screenshot_sync, url
    )
    await save_preview_image(pkid=pkid, image=screenshot_as_bytes)


async def get_weblink_metrics():
    # create a dictionary counting the number of weblinks, number of weblinks per category
    response = {"weblink_count": 0, "weblink_category_count": {}}

    try:
        data = await db_ops.read_query(Select(WebLinks))
        response["weblink_count"] = len(data)

        # count each category in data and store in response
        for item in data:
            if item.category not in response["weblink_category_count"]:
                response["weblink_category_count"][item.category] = 1
            else:
                response["weblink_category_count"][item.category] += 1

    except Exception as e:
        error: str = f"Error getting weblink metrics: {e}"
        logger.error(error)

    print(response)
    return response
