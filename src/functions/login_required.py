# -*- coding: utf-8 -*-
"""
login_required.py

Session/DB-identity checks against the shared `users` table. Generic - no
mrie-specific logic lives here; the single-admin-only restriction is enforced
at login time in endpoints/users.py, not here.

Author:
    Mike Ryan
    MIT Licensed
"""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from loguru import logger
from sqlalchemy import Select

from ..db_tables import Users
from ..resources import db_ops
from ..settings import settings


async def check_user_identifier(request):
    """
    Checks if the user identifier in the session is valid.

    Args:
        request: The request object containing the session data.

    Raises:
        HTTPException: If the user identifier is not found in the session or the user does not exist in the database.
    """
    user_identifier = request.session.get("user_identifier")

    if user_identifier is None:
        logger.error(
            f"user page access without being logged in from {request.client.host}"
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    else:
        query = Select(Users).where(Users.pkid == user_identifier)
        user = await db_ops.read_one_record(query=query)

        if user is None:
            logger.error(f"User not found with ID: {user_identifier}")
            raise HTTPException(status_code=401, detail="Unauthorized")


async def check_session_expiry(request):
    """
    Checks if the session has expired.

    Args:
        request: The request object containing the session data.

    Raises:
        HTTPException: If the session has expired or the session update time is invalid.
    """
    session_expiry_time = datetime.now() - timedelta(minutes=settings.max_age)

    try:
        last_updated = datetime.fromtimestamp(request.session.get("exp", 0))
    except ValueError:
        logger.error("Invalid session update time")
        raise HTTPException(status_code=401, detail="Unauthorized")

    current = session_expiry_time < last_updated

    if not current:
        logger.error(
            f"user {request.session.get('user_identifier')} outside window: {current}"
        )
        raise HTTPException(status_code=401, detail="Unauthorized")


async def check_login(request: Request):
    """
    Checks if the user is logged in and if the session is valid.

    Args:
        request (Request): The request object containing the session data.

    Raises:
        HTTPException: If the user is not logged in or the session has expired.

    Returns:
        dict: A dictionary containing the user's information.
    """
    request.state.user_info = {
        "user_identifier": request.session.get("user_identifier", None),
        "timezone": request.session.get("timezone", None),
        "is_admin": request.session.get("is_admin", False) is True,
        "exp": request.session.get("exp", 0),
    }

    logger.debug(f"check login initial: {request.state.user_info}")

    await check_user_identifier(request)
    await check_session_expiry(request)

    logger.debug(f"check login return: {request.state.user_info}")

    return request.state.user_info
