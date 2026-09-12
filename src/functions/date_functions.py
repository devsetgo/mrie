# -*- coding: utf-8 -*-
"""
date_functions.py

This module provides a function for converting a datetime object or string to a specified timezone.
It uses the pytz library to handle timezone conversions and the loguru library for logging.

Functions:
    timezone_update(user_timezone: str, date_time, friendly_string=False): Convert a datetime object or string to a specified timezone and optionally return it as a formatted string.

Author:
    Mike Ryan
    MIT Licensed
"""
from datetime import datetime

import pytz
from loguru import logger

TIMEZONES = pytz.all_timezones


async def timezone_update(
    user_timezone: str,
    date_time: datetime,
    friendly_string: bool = False,
    asia_format: bool = False,
):
    """
    Convert a datetime object or string to a specified timezone and optionally return it as a formatted string.

    Args:
        user_timezone (str): The timezone to convert date_time to.
        date_time (datetime or str): The datetime object or string to convert.
        friendly_string (bool): If True, return date_time as a formatted string.

    Returns:
        datetime or str: The converted datetime. If friendly_string is True, this will be a string.
    """
    # Log the input parameters
    logger.debug(
        f"Updating timezone: {user_timezone}, date_time: {date_time}, friendly_string: {friendly_string}"
    )

    # If date_time is None, return None
    if date_time is None:
        return None

    # If date_time is a string, parse it into a datetime object
    if isinstance(date_time, str):
        date_time = datetime.fromisoformat(date_time)

    # Try to convert UTC date_time to user's timezone
    try:
        user_tz = pytz.timezone(user_timezone)
        date_time = date_time.astimezone(user_tz)
    except Exception as e:
        # Log any errors that occur when updating the timezone
        logger.error(f"Error updating timezone: {e}")
        raise

    # If friendly_string is True, convert date_time to a formatted string
    if asia_format:
        date_time = date_time.strftime("%Y %b %d %H:%M:%S")
    if friendly_string:
        date_time = date_time.strftime("%d %b, %Y %I:%M %p")

    # Log the updated date_time
    logger.debug(f"Updated date_time: {date_time}")

    return date_time


async def update_timezone_for_dates(data: list, user_timezone: str):
    for d in data:
        d["date_created"] = await timezone_update(
            user_timezone=user_timezone,
            date_time=d["date_created"],
            asia_format=True,
        )
        d["date_updated"] = await timezone_update(
            user_timezone=user_timezone,
            date_time=d["date_updated"],
            asia_format=True,
        )
    return data
