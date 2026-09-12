# -*- coding: utf-8 -*-
"""
This module, `web_links.py`, serves as a part of a web application that showcases interesting weblinks, categorized and filterable by tags, date ranges, and categories. It utilizes FastAPI for routing and SQLAlchemy for database interactions, providing an API endpoint to list interesting weblinks with support for pagination.

Author:
    Mike Ryan

License:
    MIT License

Dependencies:
    - fastapi: For creating API routes and handling HTTP requests.
    - loguru: For logging.
    - sqlalchemy: For constructing and executing SQL queries.
    - datetime: For handling date and time information.
    - db_tables: Contains the SQLAlchemy table definitions for Categories and WebLinks.
    - functions: Includes utility functions and decorators, such as for AI processing and date manipulation.
    - resources: Provides access to common resources like database operations (`db_ops`) and HTML templates.

API Endpoints:
    - GET "/": Lists weblinks with optional filters applied. Supports pagination through `offset` and `limit` query parameters.
    - GET "/categories": Retrieves a list of categories for weblinks.
    - GET "/pagination": Lists weblinks with pagination and optional filters.
    - GET "/new": Displays a form to create a new interesting link.
    - POST "/new": Creates a new interesting link based on form data.


Usage:
    This module is designed to be integrated into a FastAPI application, providing a backend API for listing weblinks. It can be used in web applications that require content curation and discovery features, with the ability to filter and paginate through large sets of data.
"""
import uuid
from base64 import b64encode
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Path,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger
from sqlalchemy import Select, asc, delete, func, insert, or_, update

from ..db_tables import Categories, WebLinks, compute_weblink_ai_fix
from ..functions import ai, date_functions, link_import, link_preview
from ..functions.db_guards import (
    is_db_error,
    safe_list as _safe_list,
    safe_record as _safe_record,
)
from ..functions.login_required import check_login
from ..functions.youtube_helper import extract_youtube_video_id, is_youtube_url
from ..resources import db_ops, templates

router = APIRouter()


# api endpoints
# /list with filters (by tag, by date range, by category)
@router.get("/")
async def list_of_web_links(
    request: Request,
    offset: int = Query(0, description="Offset for pagination"),
    limit: int = Query(100, description="Limit for pagination"),
    # user_info: dict = Depends(check_login),
):
    user_timezone = request.session.get("timezone", None)
    if user_timezone is None:
        user_timezone = "America/New_York"

    weblinks_metrics = await link_preview.get_weblink_metrics()
    context = {"page": "weblinks", "weblinks_metrics": weblinks_metrics}
    return templates.TemplateResponse(
        request=request, name="/weblinks/index.html", context=context
    )


@router.get("/bulk")
async def bulk_weblink_form(
    request: Request,
    user_info: dict = Depends(check_login),
):
    return templates.TemplateResponse(
        request=request, name="weblinks/bulk.html", context={"demo_note": None}
    )


@router.post("/bulk")
async def bulk_weblink(
    background_tasks: BackgroundTasks,
    request: Request,
    csv_file: UploadFile = File(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    # user_identifier =  request.session.get("user_identifier")
    user_info["timezone"]

    # read the file content
    file_content = await csv_file.read()
    file_content = file_content.decode("utf-8")

    # await link_import.read_weblinks_from_file(
    #     csv_content=file_content, user_identifier=user_identifier
    # )
    # Add the task to background tasks
    background_tasks.add_task(
        link_import.read_weblinks_from_file,
        csv_content=file_content,
        user_identifier=user_identifier,
    )
    logger.info("Added task to background tasks")

    # redirect to /notes
    return RedirectResponse(url="/weblinks", status_code=302)


@router.get("/categories", response_class=JSONResponse)
async def get_categories():
    categories = _safe_list(
        await db_ops.read_query(
            Select(Categories)
            .where(Categories.is_weblink)
            .order_by(asc(func.lower(Categories.name)))
        )
    )
    cat_list = [cat.to_dict()["name"] for cat in categories]
    return cat_list


@router.get("/pagination")
async def read_weblinks_pagination(
    request: Request,
    search_term: str = Query(None, description="Search term"),
    start_date: str = Query(None, description="Start date"),
    end_date: str = Query(None, description="End date"),
    category: str = Query(None, description="Category"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(12, description="Number of weblinks per page"),
    # user_info: dict = Depends(check_login),
):
    query_params = {
        "search_term": search_term,
        "start_date": start_date,
        "end_date": end_date,
        "category": category,
        "limit": limit,
    }

    user_timezone = request.session.get("timezone", None)
    if user_timezone is None:
        user_timezone = "America/New_York"

    logger.info(
        f"Searching for term: {search_term}, start_date: {start_date}, end_date: {end_date}"
    )
    # find search_term in columns: note, mood, tags, summary
    query = Select(WebLinks).where(
        or_(
            WebLinks.summary.contains(search_term) if search_term else True,
            (WebLinks.title.contains(search_term) if search_term else True),
        )
    )

    # filter by date range
    if start_date and end_date:
        start_date = datetime.strptime(start_date, "%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%Y-%m-%d")
        query = query.where(
            (WebLinks.date_created >= start_date) & (WebLinks.date_created <= end_date)
        )
    if category:
        query = query.where(WebLinks.category == category)
    # order and limit the results
    offset = (page - 1) * limit
    query = query.order_by(WebLinks.date_created.desc()).limit(limit).offset(offset)
    weblinks = await db_ops.read_query(query=query)
    logger.debug(f"weblinks returned from pagination query {weblinks}")
    if not isinstance(weblinks, list):
        logger.error(f"Unexpected result from read_query: {weblinks}")
        weblinks = []
    else:
        weblinks = [link.to_dict() for link in weblinks]
    # offset date_created and date_updated to user's timezone
    for link in weblinks:
        link["date_created"] = await date_functions.timezone_update(
            user_timezone=user_timezone,
            date_time=link["date_created"],
            friendly_string=True,
        )
        link["date_updated"] = await date_functions.timezone_update(
            user_timezone=user_timezone,
            date_time=link["date_updated"],
            friendly_string=True,
        )
    found = len(weblinks)
    count_query = Select(WebLinks)
    weblinks_count = await db_ops.count_query(query=count_query)

    current_count = found

    total_pages = -(-weblinks_count // limit)  # Ceiling division
    # Generate the URLs for the previous and next pages
    prev_page_url = (
        f"/weblinks/pagination?page={page - 1}&"
        + "&".join(f"{k}={v}" for k, v in query_params.items() if v)
        if page > 1
        else None
    )
    next_page_url = (
        f"/weblinks/pagination?page={page + 1}&"
        + "&".join(f"{k}={v}" for k, v in query_params.items() if v)
        if page < total_pages
        else None
    )
    logger.info(f"Found {found} weblinks")
    context = {
        "page": "weblinks",
        "weblinks": weblinks,
        "found": found,
        "weblinks_count": weblinks_count,
        "total_pages": total_pages,
        "start_count": offset + 1,
        "current_count": offset + current_count,
        "current_page": page,
        "prev_page_url": prev_page_url,
        "next_page_url": next_page_url,
    }
    return templates.TemplateResponse(
        request=request,
        name="/weblinks/pagination.html",
        context=context,
    )


@router.get("/new")
async def new_link(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="/weblinks/new.html",
        context={"page": "weblinks", "request": request},
    )


@router.post("/new")
async def create_link(
    background_tasks: BackgroundTasks,
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]

    form = await request.form()
    category = form["category"]
    url = form["url"]
    comment = form["comment"]

    if comment == "":
        comment = None

    logger.debug(f"Received category: {category} and content: {url}")

    # Get summary from OpenAI
    summary = await ai.get_url_summary(url=url, sentence_length=20)
    title = await ai.get_url_title(url=url)
    logger.debug(f"Received summary from AI: {summary}")

    # Create the post
    new_pkid = str(uuid.uuid4())
    result = await db_ops.execute_one(
        insert(WebLinks).values(
            pkid=new_pkid,
            title=title,
            summary=summary["summary"],
            comment=comment,
            url=url,
            user_id=user_identifier,
            category=category,
            ai_fix=compute_weblink_ai_fix(
                image_preview_data=None, title=title, summary=summary["summary"]
            ),
        )
    )
    if is_db_error(result):
        logger.error(f"Error creating link: {result}")
        return RedirectResponse(url="/error/418", status_code=302)

    logger.info(f"Created weblinks with ID: {new_pkid}")

    background_tasks.add_task(
        link_preview.capture_full_page_screenshot, url=url, pkid=new_pkid
    )
    return RedirectResponse(url=f"/weblinks/view/{new_pkid}", status_code=302)


# get weblink by pkid
@router.get("/view/{pkid}")
async def view_weblink(
    request: Request,
    pkid: str,
    # user_info: dict = Depends(check_login),
):
    user_timezone = request.session.get("timezone", None)
    if user_timezone is None:
        user_timezone = "America/New_York"

    link_obj = _safe_record(
        await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
    )

    if link_obj is None:
        logger.warning(f"No weblink found with ID: {pkid}")
        return RedirectResponse(url="/error/404", status_code=303)

    # Store the is_youtube property before converting to dict
    link_is_youtube = link_obj.is_youtube
    link = link_obj.to_dict()

    # Add the is_youtube property to the dictionary
    link["is_youtube"] = link_is_youtube

    link["date_created"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=link["date_created"],
        friendly_string=True,
    )
    link["date_updated"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=link["date_updated"],
        friendly_string=True,
    )
    encoded_image = ""
    if link["image_preview_data"] is not None:
        encoded_image = b64encode(link["image_preview_data"]).decode("utf-8")

    link_status = await link_preview.url_status(url=link["url"])

    # Check if it's a YouTube link and generate embed URL
    youtube_embed_url = ""
    if is_youtube_url(link["url"]):
        video_id = extract_youtube_video_id(link["url"])
        if video_id:
            youtube_embed_url = f"https://www.youtube.com/embed/{video_id}"

    context = {
        "page": "weblinks",
        "weblink": link,
        "page_image": (
            f'<img src="data:image/png;base64,{encoded_image}" alt="{link["title"]}" class="img-thumbnail" style="width: 700px; cursor: pointer;" onclick="openModal(this)"/>'
            if encoded_image
            else ""
        ),
        "link_status": link_status,
        "youtube_embed_url": youtube_embed_url,
    }
    return templates.TemplateResponse(
        request=request, name="/weblinks/view.html", context=context
    )


@router.get("/update/{pkid}")
async def edit_weblink(
    background_tasks: BackgroundTasks,
    pkid: str,
    request: Request,
    user_info: dict = Depends(check_login),
):
    # user_identifier = user_info["user_identifier"]

    existing = _safe_record(
        await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
    )
    if existing is None:
        logger.warning(f"No weblink found with ID: {pkid}")
        return RedirectResponse(url="/error/404", status_code=302)

    existing = existing.to_dict()

    url = existing["url"]

    logger.debug(f"Editing content for URL: {url}")

    # Get summary from OpenAI
    summary = await ai.get_url_summary(url=url, sentence_length=20)
    title = await ai.get_url_title(url=url)
    logger.debug(f"Received summary from AI: {summary}")
    weblink_update = {
        "title": title,
        "summary": summary["summary"],
        "ai_fix": compute_weblink_ai_fix(
            image_preview_data=existing["image_preview_data"],
            title=title,
            summary=summary["summary"],
        ),
    }

    result = await db_ops.execute_one(
        update(WebLinks).where(WebLinks.pkid == pkid).values(**weblink_update)
    )

    if is_db_error(result):
        logger.error(f"Error updating link: {result}")
        return RedirectResponse(url="/error/418", status_code=302)

    logger.info(f"Updated weblinks with ID: {pkid}")

    background_tasks.add_task(
        link_preview.capture_full_page_screenshot, url=url, pkid=pkid
    )
    return RedirectResponse(url=f"/weblinks/view/{pkid}", status_code=302)


@router.get("/update/comment/{pkid}")
async def get_update_comment(
    pkid: str,
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_info["user_identifier"]

    link = _safe_record(
        await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
    )
    if link is None:
        logger.warning(f"No weblink found with ID: {pkid}")
        return RedirectResponse(url="/error/404", status_code=302)

    # Get categories for the dropdown
    categories = _safe_list(
        await db_ops.read_query(
            Select(Categories)
            .where(Categories.is_weblink)
            .order_by(asc(func.lower(Categories.name)))
        )
    )
    cat_list = [cat.to_dict()["name"] for cat in categories]

    link = link.to_dict()
    context = {
        "weblink": link,
        "categories": cat_list,
    }
    return templates.TemplateResponse(
        request=request, name="/weblinks/edit-comment.html", context=context
    )


@router.post("/update/comment/{pkid}")
async def update_comment(
    background_tasks: BackgroundTasks,
    pkid: str,
    request: Request,
    user_info: dict = Depends(check_login),
):
    form = await request.form()
    comment = form["comment"]
    url = form["url"]
    category = form["category"]
    public = form.get("public") is not None
    logger.debug(
        f"Received comment: {comment}, url: {url}, category: {category}, public: {public}"
    )

    weblink_update = {
        "comment": comment,
        "url": url,
        "category": category,
        "public": public,
    }

    result = await db_ops.execute_one(
        update(WebLinks).where(WebLinks.pkid == pkid).values(**weblink_update)
    )

    if is_db_error(result):
        logger.error(f"Error updating link: {result}")
        return RedirectResponse(url="/error/418", status_code=302)

    logger.info(f"Updated weblinks with ID: {pkid}")
    background_tasks.add_task(
        link_preview.capture_full_page_screenshot, url=url, pkid=pkid
    )
    return RedirectResponse(url=f"/weblinks/view/{pkid}", status_code=302)


@router.get("/delete/{pkid}")
async def delete_weblink_form(
    request: Request,
    pkid: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]

    # Check if the weblink exists and belongs to the user
    link = _safe_record(
        await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
    )

    if link is None:
        logger.warning(f"No weblink found with ID: {pkid}")
        return RedirectResponse(url="/error/404", status_code=302)

    # Check if user owns the weblink or is admin
    if link.user_id != user_identifier and not user_info.get("is_admin", False):
        logger.warning(
            f"User {user_identifier} attempted to access weblink {pkid} owned by {link.user_id}"
        )
        return RedirectResponse(url="/error/403", status_code=302)

    context = {"weblink": link.to_dict()}
    return templates.TemplateResponse(
        request=request, name="weblinks/delete.html", context=context
    )


@router.post("/delete/{pkid}")
async def delete_weblink(
    request: Request,
    pkid: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]

    # Process the form data
    try:
        form = await request.form()
        delete_confirm = form.get("deleteConfirm")

        if not delete_confirm:
            logger.warning(f"Delete confirmation not checked for weblink {pkid}")
            return RedirectResponse(url="/error/400", status_code=302)

    except Exception as e:
        logger.error(f"Error processing form data for weblink delete {pkid}: {str(e)}")
        return RedirectResponse(url="/error/400", status_code=302)

    # Check if the weblink exists and belongs to the user
    link = _safe_record(
        await db_ops.read_one_record(Select(WebLinks).where(WebLinks.pkid == pkid))
    )

    if link is None:
        logger.warning(f"No weblink found with ID: {pkid} for user: {user_identifier}")
        return RedirectResponse(url="/error/404", status_code=302)

    # Check if user owns the weblink or is admin
    if link.user_id != user_identifier and not user_info.get("is_admin", False):
        logger.warning(
            f"User {user_identifier} attempted to delete weblink {pkid} owned by {link.user_id}"
        )
        return RedirectResponse(url="/error/403", status_code=302)

    try:
        # Attempt to delete the weblink
        result = await db_ops.execute_one(delete(WebLinks).where(WebLinks.pkid == pkid))

        # Check if the result indicates an error
        if is_db_error(result):
            logger.error(f"Error deleting weblink {pkid}: {result}")
            return RedirectResponse(url="/error/500", status_code=302)

        logger.info(
            f"Successfully deleted weblink with ID: {pkid} by user: {user_identifier}"
        )
        return RedirectResponse(url="/weblinks", status_code=302)

    except Exception as e:
        logger.error(f"Exception occurred while deleting weblink {pkid}: {str(e)}")
        return RedirectResponse(url="/error/500", status_code=302)
