# -*- coding: utf-8 -*-
""" """

import httpx
from loguru import logger
from sqlalchemy import Select, update

from ..db_tables import WebLinks, compute_weblink_ai_fix
from ..functions import ai
from ..functions._optional_deps import tqdm, unsync
from ..functions.db_guards import is_db_error, safe_record as _safe_record
from ..resources import db_ops

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
    from .youtube_helper import is_youtube_url

    if webdriver is None:
        raise RuntimeError(
            "selenium/webdriver-manager not installed - screenshot capture disabled"
        )

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
            import time

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
        screenshot_as_bytes = driver.get_screenshot_as_png()
        await save_preview_image(pkid=pkid, image=screenshot_as_bytes)
    finally:
        driver.quit()


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


async def update_weblinks_ai(list_of_ids: list):
    # for pkid in tqdm(list_of_ids):
    #     print(pkid)
    tasks = [
        update_weblinks(pkid=pkid)
        for pkid in tqdm(list_of_ids, ascii=False, leave=True, desc="Sending Weblinks")
    ]
    results = [
        task.result()
        for task in tqdm(tasks, ascii=False, leave=True, desc="Updating Weblinks")
    ]
    logger.info(f"Weblink AI Fix Results: {results}")
    return None


@unsync
async def update_weblinks(pkid: str):
    try:
        data = _safe_record(
            await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
        )
        logger.debug(f"Received data from DB: {data}")
        if data is None:
            logger.error(f"Error reading link {pkid}")
        else:
            summary = await ai.get_url_summary(url=data.url, sentence_length=20)
            title = await ai.get_url_title(url=data.url)
            logger.debug(f"Received summary from AI: {summary}")
            weblink_update = {
                "title": title,
                "summary": summary["summary"],
                "ai_fix": compute_weblink_ai_fix(
                    image_preview_data=data.image_preview_data,
                    title=title,
                    summary=summary["summary"],
                ),
            }
            logger.debug(f"Updating weblinks: {weblink_update}")
            result = await db_ops.execute_one(
                update(WebLinks).where(WebLinks.pkid == pkid).values(**weblink_update)
            )
            logger.debug(f"Updated weblinks: {result}")
            if is_db_error(result):
                logger.error(f"Error updating weblink: {result}")

            logger.info(f"Created weblinks with ID: {pkid}")

            await capture_full_page_screenshot(url=data.url, pkid=pkid)
            return "complete"
    except Exception as e:
        error = f"Error updating weblinks: {e}"
        logger.error(error)
        return "error"
