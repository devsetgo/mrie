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
from sqlalchemy import Select, insert, update

from ..db_tables import WebLinks, compute_weblink_ai_fix
from ..functions import ai, link_preview
from ..functions._optional_deps import tqdm
from ..functions.db_guards import safe_record as _safe_record
from ..resources import db_ops

# class WebLink(BaseModel):
#     user_id: str
#     url: str
#     category: str
#     ai_fixe: bool = True


async def read_weblinks_from_file(csv_content: str, user_identifier: str):
    """
    Read weblinks from a CSV content string
    """
    csv_reader = csv.DictReader(io.StringIO(csv_content))
    data = list(csv_reader)

    link_pkids: list = []
    count = 0
    for i in data:
        count += 1
        # if count > 10:
        #     break
        public = True if i["public"] == "True" else False
        title = await ai.get_html_title(i["url"])
        if title == "Not Found":
            title = "Processing"
        new_pkid = str(uuid.uuid4())
        await db_ops.execute_one(
            insert(WebLinks).values(
                pkid=new_pkid,
                title=title,
                summary="Processing",
                url=i["url"],
                category=i["category"],
                public=public,
                user_id=user_identifier,
                # image_preview_data is always None at this point (screenshot
                # capture happens later in loop_pkids_for_images), so this is
                # always True - see compute_weblink_ai_fix in db_tables.py.
                ai_fix=compute_weblink_ai_fix(
                    image_preview_data=None, title=title, summary="Processing"
                ),
            )
        )
        logger.debug(new_pkid)
        link_pkids.append(new_pkid)

    logger.debug(link_pkids)
    await ai_process_pkids(link_pkids)
    await loop_pkids_for_images(link_pkids)


async def ai_process_pkids(pkids: list):
    """
    Process the AI for the given pkids
    """
    for pkid in tqdm(pkids, desc="AI Processing links"):
        record = _safe_record(
            await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
        )

        if record is None:
            logger.error(f"Error reading link {pkid}")
            continue

        link_update = {}
        # title = await ai.get_html_title(record.url)
        logger.info(f"Processed link {record.pkid}")
        summary = await ai.get_url_summary(record.url)
        link_update["summary"] = summary["summary"]

        if record.title == "Processing":
            link_update["title"] = await ai.get_url_title(record.url)

        # image_preview_data isn't touched here (screenshot capture happens
        # afterward in loop_pkids_for_images) - recompute ai_fix from the
        # post-update field values the same way the before_update ORM event
        # used to, rather than hardcoding False, so a link isn't marked
        # "fixed" before its screenshot actually exists.
        link_update["ai_fix"] = compute_weblink_ai_fix(
            image_preview_data=record.image_preview_data,
            title=link_update.get("title", record.title),
            summary=link_update["summary"],
        )

        logger.info(link_update)
        result = await db_ops.execute_one(
            update(WebLinks).where(WebLinks.pkid == pkid).values(**link_update)
        )
        logger.debug(f"Updated link data: {result}")

    return {"status": "success"}


# link_preview.capture_full_page_screenshot, url=url, pkid=data.pkid
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
