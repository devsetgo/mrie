# -*- coding: utf-8 -*-
"""

Author:
    Mike Ryan
    MIT Licensed
"""

import csv
import io
import uuid

from loguru import logger
from sqlalchemy import Select, insert

from ..db_tables import WEBLINK_PROCESSING, WebLinks
from ..functions import link_analysis, link_preview
from ..functions._optional_deps import tqdm
from ..functions.db_guards import safe_record as _safe_record
from ..resources import db_ops


async def read_weblinks_from_file(csv_content: str, user_identifier: str):
    """
    Read weblinks from a CSV content string
    """
    csv_reader = csv.DictReader(io.StringIO(csv_content))
    data = list(csv_reader)

    link_pkids: list = []
    for i in data:
        public = True if i["public"] == "True" else False
        new_pkid = str(uuid.uuid4())
        await db_ops.execute_one(
            insert(WebLinks).values(
                pkid=new_pkid,
                title=WEBLINK_PROCESSING,
                summary=WEBLINK_PROCESSING,
                url=i["url"],
                category=i["category"],
                public=public,
                user_id=user_identifier,
                # Placeholders until ai_process_pkids fills them in.
                ai_fix=True,
            )
        )
        logger.debug(new_pkid)
        link_pkids.append(new_pkid)

    logger.debug(link_pkids)
    await ai_process_pkids(link_pkids)
    await loop_pkids_for_images(link_pkids)


async def ai_process_pkids(pkids: list):
    """
    Fill in each imported link's title/summary, one at a time. Screenshots
    come afterwards in loop_pkids_for_images.
    """
    for pkid in tqdm(pkids, desc="AI Processing links"):
        record = _safe_record(
            await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
        )
        if record is None:
            logger.error(f"Error reading link {pkid}")
            continue

        # Never raises; AI unavailable leaves the placeholders and moves on.
        await link_analysis.process_link_background(
            pkid=pkid, url=record.url, capture_screenshot=False
        )

    return {"status": "success"}


async def loop_pkids_for_images(pkids: list):
    """
    Loop through the pkids and capture the full page screenshot
    """
    for pkid in tqdm(pkids, desc="Capturing full page screenshots"):
        data = await db_ops.read_one_record(
            Select(WebLinks).where(WebLinks.pkid == pkid)
        )
        if isinstance(data, dict):
            logger.error(f"Error creating link: {data}")
        else:
            await link_preview.capture_full_page_screenshot(
                url=data.url, pkid=data.pkid
            )

    return {"status": "success"}
