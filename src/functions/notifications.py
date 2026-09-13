# -*- coding: utf-8 -*-
import uuid

from loguru import logger
from sqlalchemy import insert

from ..db_tables import Notifications
from ..resources import db_ops


async def create_notification(
    user_id: str,
    message: str,
    category: str = "info",
    note_id: str = None,
) -> None:
    try:
        await db_ops.execute_one(
            insert(Notifications).values(
                pkid=str(uuid.uuid4()),
                user_id=user_id,
                message=message,
                category=category,
                note_id=note_id,
            )
        )
        logger.debug(f"Notification created for user {user_id}: {message[:60]}")
    except Exception as e:
        logger.error(f"Failed to create notification for user {user_id}: {e}")
