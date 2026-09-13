# -*- coding: utf-8 -*-
"""
This module, `notes.py`, is designed for handling note-taking functionalities within a web application. It provides the capability to create, read, update, and delete notes. Additionally, it supports importing notes from CSV files and offers time-based features, such as setting reminders for notes. The module leverages FastAPI for creating API endpoints and utilizes Python's built-in `datetime` module for time-related operations.

Author:
    Mike Ryan

License:
    MIT License

Dependencies:
    - fastapi: For creating API routes and handling HTTP requests.
    - csv: For parsing CSV files during note import operations.
    - io: For handling in-memory file operations, useful in parsing CSV content.
    - datetime: For managing dates and times, including support for time zones and time deltas.

Features:
    - CRUD operations for notes.
    - Importing notes from CSV files.
    - Setting reminders and deadlines for notes.

Usage:
    This module is intended to be used as part of a larger FastAPI application. It can be mounted as a router to handle note-related routes, providing a RESTful API for managing notes.

Routes:
    - GET /: Dashboard - insights/charts for the user's notes.
    - GET /list: Browse/search notes page.
    - GET /metrics/counts: Retrieves metrics related to the user's notes.
    - GET /ai-resubmit/{note_id}: Displays a form to resubmit a note for AI processing.
    - GET /ai-fix/{note_id}: Initiates AI processing to fix a note's content.
    - GET /bulk: Displays a form for bulk note import.
    - POST /bulk: Handles bulk note import from a CSV file.
    - GET /edit/{note_id}: Displays a form to edit a note.
    - POST /edit/{note_id}: Updates a note with new content.
    - GET /delete/{note_id}: Displays a form to delete a note.
    - POST /delete/{note_id}: Deletes a note.
    - GET /issues: Retrieves notes with AI issues.
    - GET /new: Displays a form to create a new note.
    - POST /new: Creates a new note.
    - GET /pagination: Retrieves paginated notes based on search criteria.
    - GET /today: Retrieves notes for today's date.
    - GET /view/{note_id}: Displays a single note.
"""

import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

# from pytz import timezone, UTC
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
from fastapi.responses import RedirectResponse
from loguru import logger
from sqlalchemy import (
    Select,
    Text,
    and_,
    between,
    cast,
    delete,
    desc,
    extract,
    func,
    insert,
    or_,
    update,
)

from ..db_tables import NoteMetrics, Notes, compute_note_derived_fields
from ..functions import ai, date_functions, note_import, notes_metrics
from ..functions.notifications import create_notification
from ..functions.db_guards import (
    is_db_error,
    safe_list as _safe_list,
    safe_record as _safe_record,
)
from ..functions.encrypt import encrypt_text
from ..functions.login_required import check_login
from ..resources import db_ops, templates
from ..settings import settings

router = APIRouter()


async def process_ai_analysis_background(
    note_id: str, content: str, user_id: str, mood_process: str | None = None
):
    try:
        logger.info(f"Starting background AI analysis for note {note_id}")

        analysis = await ai.get_analysis(content=content, mood_process=mood_process)

        moods_list: list = [mood[0] for mood in settings.mood_analysis_weights]
        ai_fix = analysis["mood_analysis"] not in moods_list or analysis.get(
            "_ai_fix", False
        )

        # mood is the user's self-reported feeling — AI must never overwrite it.
        # mood_analysis is the AI's nuanced assessment (elated, hopeless, etc.).
        await db_ops.execute_one(
            update(Notes)
            .where(Notes.pkid == note_id)
            .values(
                tags=analysis["tags"]["tags"],
                _summary=encrypt_text(analysis["summary"]),
                mood_analysis=analysis["mood_analysis"],
                ai_fix=ai_fix,
            )
        )

        tags = ", ".join(analysis["tags"]["tags"]) or "none"
        mood = analysis["mood_analysis"]
        await create_notification(
            user_id=user_id,
            message=f"AI analysis complete — mood: {mood}, tags: {tags}",
            category="ai",
            note_id=note_id,
        )
        logger.info(f"Completed background AI analysis for note {note_id}")

    except Exception as e:
        logger.error(f"Error in background AI analysis for note {note_id}: {str(e)}")
        await db_ops.execute_one(
            update(Notes).where(Notes.pkid == note_id).values(ai_fix=True)
        )
        await create_notification(
            user_id=user_id,
            message="AI analysis failed for a note. Retry from the AI Issues page.",
            category="error",
            note_id=note_id,
        )


@router.get("/")
async def read_notes(
    background_tasks: BackgroundTasks,
    request: Request,
    offset: int = Query(0, description="Offset for pagination"),
    limit: int = Query(100, description="Limit for pagination"),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]

    if user_identifier is None:
        logger.debug("User identifier is None, redirecting to login")
        return RedirectResponse(url="/users/login", status_code=302)

    note_metrics = _safe_record(
        await db_ops.read_one_record(
            query=Select(NoteMetrics).where(NoteMetrics.user_id == user_identifier)
        )
    )

    if note_metrics is None:
        await notes_metrics.update_notes_metrics(user_id=user_identifier)
        note_metrics = _safe_record(
            await db_ops.read_one_record(
                query=Select(NoteMetrics).where(NoteMetrics.user_id == user_identifier)
            )
        )

    metrics = None

    if note_metrics is not None:
        note_metrics = note_metrics.to_dict()
        # note_metrics.pop("pkid")
        # note_metrics.pop("date_created")
        # # note_metrics.pop("date_updated")
        # note_metrics.pop("user_id")
        metrics = note_metrics["metrics"]

        # Get the current time in UTC
        now = datetime.utcnow()

        # Calculate the time one hour ago
        nth_hour_ago = now - timedelta(hours=0.25)

        # Get date_updated from note_metrics
        date_update = note_metrics["date_updated"]

        # Check if date_update is older than one hour
        if date_update < nth_hour_ago:
            background_tasks.add_task(
                notes_metrics.update_notes_metrics, user_id=user_identifier
            )
    logger.info(f"User {user_identifier} dashboard retrieved")
    context = {
        "page": "notes",
        "user_identifier": user_identifier,
        "metrics": metrics,
        "note_metrics": note_metrics,
        "mood_takeaway_months": user_info.get("mood_takeaway_months", 3),
    }
    # print(context["note_metrics"]["ai_fix_count"])
    return templates.TemplateResponse(
        request=request, name="/notes/dashboard.html", context=context
    )


@router.get("/list")
async def list_notes(
    request: Request,
    user_info: dict = Depends(check_login),
):
    return templates.TemplateResponse(
        request=request, name="/notes/list.html", context={}
    )


@router.get("/metrics/counts")
async def get_note_counts(
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]

    note_metrics = _safe_record(
        await db_ops.read_one_record(
            query=Select(NoteMetrics).where(NoteMetrics.user_id == user_identifier)
        )
    )
    if note_metrics is None:
        await notes_metrics.update_notes_metrics(user_id=user_identifier)
        note_metrics = _safe_record(
            await db_ops.read_one_record(
                query=Select(NoteMetrics).where(NoteMetrics.user_id == user_identifier)
            )
        )

    if note_metrics is None:
        logger.error(f"Unable to load note metrics for user {user_identifier}")
        return templates.TemplateResponse(
            request=request,
            name="/notes/metrics.html",
            context={"note_metrics": {}},
        )

    note_metrics = note_metrics.to_dict()
    for field in ["pkid", "date_created", "date_updated", "user_id"]:
        note_metrics.pop(field, None)

    logger.debug(f"User {user_identifier} fetched note counts: {note_metrics}")
    logger.info(f"User {user_identifier} metrics retrieved")

    return templates.TemplateResponse(
        request=request,
        name="/notes/metrics.html",
        context={"note_metrics": note_metrics},
    )


@router.get("/ai-resubmit/{note_id}")
async def ai_update_note(
    request: Request, note_id: str, user_info: dict = Depends(check_login)
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]

    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))

    if note is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/error/404", status_code=303)

    note = note.to_dict()

    return templates.TemplateResponse(
        request=request, name="/notes/ai-resubmit.html", context={"note": note}
    )


@router.get("/ai-fix/{note_id}")
async def ai_fix_processing(
    background_tasks: BackgroundTasks,
    request: Request,
    note_id: str,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]

    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))
    if note is None:
        return RedirectResponse(url="/error/404", status_code=303)

    note_dict = note.to_dict()
    mood = (
        note_dict["mood"]
        if note_dict["mood"] in ["positive", "negative", "neutral"]
        else None
    )

    # Clear the flag immediately so the note leaves /issues before the background task finishes
    await db_ops.execute_one(
        update(Notes).where(Notes.pkid == note_id).values(ai_fix=False)
    )

    background_tasks.add_task(
        process_ai_analysis_background,
        note_id=note_id,
        content=note_dict["note"],
        user_id=user_identifier,
        mood_process=mood,
    )
    logger.info(f"Queued background AI fix for note {note_id}")
    return RedirectResponse(url=f"/notes/view/{note_id}", status_code=302)


@router.get("/bulk")
async def bulk_note_form(
    request: Request,
    # user_info: dict = Depends(check_login),
):
    return templates.TemplateResponse(
        request=request, name="notes/bulk.html", context={"demo_note": None}
    )


@router.post("/bulk")
async def bulk_note(
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
    logger.debug(f"Received file content: {file_content}")
    csv_reader = csv.DictReader(io.StringIO(file_content))
    logger.debug(f"Reading notes from file for user: {user_identifier}")
    # add the task to background tasks
    background_tasks.add_task(
        note_import.read_notes_from_file, csv_file=csv_reader, user_id=user_identifier
    )
    logger.info("Added task to background tasks")

    # redirect to /notes
    return RedirectResponse(url="/notes", status_code=302)


@router.get("/edit/{note_id}")
async def edit_note_form(
    request: Request,
    note_id: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_timezone = user_info["timezone"]

    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))
    if note is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/notes", status_code=302)

    note = note.to_dict()

    # offset date_created and date_updated to user's timezone
    note["date_created"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=note["date_created"],
        friendly_string=True,
    )
    note["date_updated"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=note["date_updated"],
        friendly_string=True,
    )

    logger.info(
        f"Returning edit form for note with ID: {note_id} for user: {user_identifier}"
    )

    return templates.TemplateResponse(
        request=request,
        name="/notes/edit.html",
        context={"note": note, "mood_analysis": ai.mood_analysis},
    )


@router.post("/edit/{note_id}")
async def update_note(
    background_tasks: BackgroundTasks,
    request: Request,
    note_id: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]

    # Fetch the old data
    old_data = _safe_record(
        await db_ops.read_one_record(query=Select(Notes).where(Notes.pkid == note_id))
    )
    if old_data is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/notes", status_code=302)
    old_data = old_data.to_dict()

    # Get the new data from the form
    form = await request.form()

    # Initialize the updated data dictionary with the current date and time
    updated_data = {"date_updated": datetime.utcnow()}

    # List of fields to update
    fields = ["mood", "note", "tags", "summary", "mood_analysis"]
    # Compare the old data to the new data
    for field in fields:
        new_value = form.get(field)
        if new_value is not None and new_value != old_data.get(field):
            if field == "tags":
                # Ensure tags is a list
                if isinstance(new_value, str):
                    new_value = [tag.strip() for tag in new_value.split(",")]
                elif not isinstance(new_value, list):
                    new_value = [new_value]
            updated_data[field] = new_value

    # word_count/character_count/ai_fix/demo_created used to be recomputed
    # automatically by the before_update ORM event whenever *any* field
    # changed (see compute_note_derived_fields in db_tables.py) - a Core
    # execute_one(update(...)) bypasses that entirely, so recompute
    # explicitly here from the resulting (old + changed) field values,
    # matching what the event would have seen.
    derived = compute_note_derived_fields(
        note=updated_data.get("note", old_data["note"]),
        mood=updated_data.get("mood", old_data["mood"]),
        mood_analysis=updated_data.get("mood_analysis", old_data["mood_analysis"]),
        tags=updated_data.get("tags", old_data["tags"]),
        demo_created=old_data["demo_created"],
    )
    updated_data.update(derived)

    # Notes.note/summary are Python properties that encrypt on assignment
    # (see db_tables.py) - only via ORM-instance construction, not a
    # Core-level update().values(), which only recognizes the real mapped
    # columns (_note/_summary). Encrypt explicitly when either changed.
    if "note" in updated_data:
        updated_data["_note"] = encrypt_text(updated_data.pop("note"))
    if "summary" in updated_data:
        updated_data["_summary"] = encrypt_text(updated_data.pop("summary"))

    # Update the database
    result = await db_ops.execute_one(
        update(Notes).where(Notes.pkid == note_id).values(**updated_data)
    )
    if is_db_error(result):
        logger.error(f"Failed to update note with ID: {note_id}")
        return RedirectResponse(url="/error/500", status_code=303)
    logger.info(f"Updated note with ID: {note_id}")
    background_tasks.add_task(
        notes_metrics.update_notes_metrics, user_id=user_identifier
    )
    return RedirectResponse(url=f"/notes/view/{note_id}", status_code=302)


@router.get("/delete/{note_id}")
async def delete_note_form(
    request: Request,
    note_id: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]
    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))
    if note is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/notes", status_code=302)

    return templates.TemplateResponse(
        request=request, name="/notes/delete.html", context={"note": note}
    )


@router.post("/delete/{note_id}")
async def delete_note(
    background_tasks: BackgroundTasks,
    request: Request,
    note_id: str = Path(...),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]

    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))

    if note is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/notes", status_code=302)

    await db_ops.execute_one(delete(Notes).where(Notes.pkid == note_id))

    logger.info(f"Deleted note with ID: {note_id}")
    background_tasks.add_task(
        notes_metrics.update_notes_metrics, user_id=user_identifier
    )
    return RedirectResponse(url="/notes", status_code=302)


@router.get("/issues")
async def get_note_issue(
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_timezone = user_info["timezone"]

    if user_identifier is None:
        logger.debug("User identifier is None, redirecting to login")
        return RedirectResponse(url="/users/login", status_code=302)

    query = (
        Select(Notes)
        .where(and_(Notes.user_id == user_identifier, Notes.ai_fix))
        .limit(200)
    ).order_by(desc(Notes.date_created))
    notes = _safe_list(await db_ops.read_query(query=query))

    # offset date_created and date_updated to user's timezone
    notes = [note.to_dict() for note in notes]
    metrics = {"word_count": 0, "note_count": len(notes), "character_count": 0}
    notes = await date_functions.update_timezone_for_dates(
        data=notes, user_timezone=user_timezone
    )

    logger.info(f"Found {len(notes)} notes for user {user_identifier}")

    return templates.TemplateResponse(
        request=request,
        name="/notes/issues.html",
        context={"notes": notes, "metrics": metrics},
    )


@router.get("/new")
async def new_note_form(
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_info["user_identifier"]
    user_info["timezone"]

    return templates.TemplateResponse(
        request=request, name="notes/new.html", context={}
    )


@router.post("/new")
async def create_note(
    background_tasks: BackgroundTasks,
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_info["timezone"]
    form = await request.form()
    mood = form["mood"]
    note_content = form["note"]

    logger.debug(f"Received mood: {mood} and note: {note_content}")

    # Create the note immediately with minimal data
    # AI analysis will be done in the background
    new_note_pkid = str(uuid.uuid4())
    derived = compute_note_derived_fields(
        note=note_content,
        mood=mood,
        mood_analysis="processing",
        tags=[],
        demo_created=0,
    )
    result = await db_ops.execute_one(
        insert(Notes).values(
            pkid=new_note_pkid,
            mood=mood,
            _note=encrypt_text(note_content),
            _summary=encrypt_text(
                "Processing..."
            ),  # Will be updated by background task
            tags=[],  # Will be populated by background task
            mood_analysis="processing",  # Will be updated by background task
            user_id=user_identifier,
            **derived,
        )
    )
    if is_db_error(result):
        logger.error(f"Failed to create note for user {user_identifier}")
        return RedirectResponse(url="/error/500", status_code=303)

    # Add background tasks for AI analysis and metrics update
    background_tasks.add_task(
        process_ai_analysis_background,
        note_id=new_note_pkid,
        content=note_content,
        user_id=user_identifier,
    )
    background_tasks.add_task(
        notes_metrics.update_notes_metrics, user_id=user_identifier
    )

    logger.info(
        f"Created note with ID: {new_note_pkid}, AI analysis running in background"
    )

    return RedirectResponse(url=f"/notes/view/{new_note_pkid}", status_code=302)


@router.get("/tags")
async def get_note_tags(
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    # Select only the tags column — avoids loading encrypted note/summary blobs
    query = Select(Notes.tags).where(Notes.user_id == user_identifier)
    results = await db_ops.read_query(query=query)
    all_tags: set = set()

    def _extract_tags(row):
        if row is None:
            return None

        if hasattr(row, "tags"):
            return row.tags

        mapping = getattr(row, "_mapping", None)
        if mapping and "tags" in mapping:
            return mapping["tags"]

        if isinstance(row, dict):
            return row.get("tags")

        if isinstance(row, (list, tuple)):
            return row[0] if row else None

        return None

    if results and not isinstance(results, (str, dict)):
        for row in results:
            tags_value = _extract_tags(row)
            if not tags_value:
                continue

            if isinstance(tags_value, str):
                try:
                    parsed = json.loads(tags_value)
                    tags_value = parsed if isinstance(parsed, list) else [tags_value]
                except Exception:
                    tags_value = [tags_value]

            if isinstance(tags_value, (list, tuple, set)):
                all_tags.update([tag for tag in tags_value if tag])
    return sorted(all_tags)


@router.get("/pagination")
async def read_notes_pagination(
    request: Request,
    search_term: str = Query(
        None, description="Search term for note and summary content"
    ),
    start_date: str = Query(None, description="Start date"),
    end_date: str = Query(None, description="End date"),
    mood: str = Query(None, description="Mood"),
    tags: list[str] = Query(default=[], description="Tags to filter by (OR logic)"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(20, description="Number of notes per page"),
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_timezone = user_info["timezone"]

    logger.info(
        f"Searching content: {search_term!r}, tags: {tags}, start_date: {start_date}, "
        f"end_date: {end_date}, mood: {mood}, user: {user_identifier}"
    )

    # Base SQL query — mood, dates, and tags are filterable at the DB level
    query = Select(Notes).where(Notes.user_id == user_identifier)

    # Tag filter: OR logic, case-insensitive exact match within the JSON array.
    # func.lower() on both sides handles mixed-case stored tags (e.g. "Valerie" matches "valerie").
    if tags:

        def _tag_like(tag: str):
            escaped = (
                tag.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            return func.lower(cast(Notes.tags, Text)).like(
                f'%"{escaped}"%', escape="\\"
            )

        query = query.where(or_(*[_tag_like(t) for t in tags]))

    if mood:
        query = query.where(Notes.mood == mood.lower())

    if start_date or end_date:
        start_dt = (
            datetime.strptime(start_date, "%Y-%m-%d")
            if start_date
            else datetime(2011, 1, 1)
        )
        end_dt = (
            datetime.strptime(end_date, "%Y-%m-%d")
            if end_date
            else datetime.now(timezone.utc) + timedelta(days=2)
        )
        query = query.where(
            (Notes.date_created >= start_dt) & (Notes.date_created <= end_dt)
        )

    query = query.order_by(Notes.date_created.desc())

    offset = (page - 1) * limit

    if not search_term:
        # Fast path: no content decryption needed — use DB-level pagination
        note_count = await db_ops.count_query(query=query)
        total_pages = -(-note_count // limit) if note_count else 1
        page_notes = await db_ops.read_query(query=query.limit(limit).offset(offset))
        if not isinstance(page_notes, list):
            logger.error(f"Unexpected result from read_query: {page_notes}")
            page_notes = []
        notes = [n.to_dict() for n in page_notes]
    else:
        # Slow path: decrypt all SQL-filtered notes to search content
        # For ~2,900–5,800 notes Fernet decrypt is fast (~100ms total); the
        # mood/date/tag SQL filters above reduce the set before we get here.
        all_notes = await db_ops.read_query(query=query)
        if not isinstance(all_notes, list):
            logger.error(f"Unexpected result from read_query: {all_notes}")
            all_notes = []
        term = search_term.lower()
        all_notes = [
            n
            for n in all_notes
            if term in (n.note or "").lower() or term in (n.summary or "").lower()
        ]
        note_count = len(all_notes)
        total_pages = -(-note_count // limit) if note_count else 1
        notes = [n.to_dict() for n in all_notes[offset : offset + limit]]

    notes = await date_functions.update_timezone_for_dates(
        data=notes, user_timezone=user_timezone
    )

    found = len(notes)

    def _page_url(p: int) -> str:
        pairs = [("page", p)]
        if search_term:
            pairs.append(("search_term", search_term))
        if start_date:
            pairs.append(("start_date", start_date))
        if end_date:
            pairs.append(("end_date", end_date))
        if mood:
            pairs.append(("mood", mood))
        if limit != 20:
            pairs.append(("limit", limit))
        for tag in tags:
            pairs.append(("tags", tag))
        return "/notes/pagination?" + urlencode(pairs)

    prev_page_url = _page_url(page - 1) if page > 1 else None
    next_page_url = _page_url(page + 1) if page < total_pages else None

    logger.info(
        f"Found {note_count} notes for user {user_identifier} (page {page}/{total_pages})"
    )
    return templates.TemplateResponse(
        request=request,
        name="/notes/pagination.html",
        context={
            "user_identifier": user_identifier,
            "notes": notes,
            "found": found,
            "note_count": note_count,
            "total_pages": total_pages,
            "start_count": offset + 1 if found else 0,
            "current_count": offset + found,
            "current_page": page,
            "prev_page_url": prev_page_url,
            "next_page_url": next_page_url,
        },
    )


# today in history notes list
@router.get("/today")
async def read_today_notes(
    request: Request,
    user_info: dict = Depends(check_login),
):
    user_identifier = user_info["user_identifier"]
    user_timezone = user_info["timezone"]

    if user_identifier is None:
        logger.debug("User identifier is None, redirecting to login")
        return RedirectResponse(url="/users/login", status_code=302)

    # get today's date
    today = datetime.now(timezone.utc)

    # calculate the dates for 7 days before and after today
    start_date = today - timedelta(days=settings.history_range)
    end_date = today + timedelta(days=settings.history_range)

    # query = Select(Notes).where(
    #     and_(
    #         Notes.user_id == user_identifier,
    #         between(Notes.date_created, start_date, end_date),
    #     )
    # )
    query = (
        Select(Notes)
        .where(
            and_(
                Notes.user_id == user_identifier,
                or_(
                    and_(
                        extract("month", Notes.date_created) == start_date.month,
                        between(
                            extract("day", Notes.date_created),
                            start_date.day,
                            end_date.day,
                        ),
                    ),
                    and_(
                        extract("month", Notes.date_created) == end_date.month,
                        between(
                            extract("day", Notes.date_created),
                            start_date.day,
                            end_date.day,
                        ),
                    ),
                ),
            )
        )
        .order_by(desc(Notes.date_created))
    )
    notes = _safe_list(await db_ops.read_query(query=query))

    # offset date_created and date_updated to user's timezone
    notes = [note.to_dict() for note in notes]

    metrics = {"word_count": 0, "note_count": len(notes), "character_count": 0}
    notes = await date_functions.update_timezone_for_dates(
        data=notes, user_timezone=user_timezone
    )
    logger.info(f"Found {len(notes)} notes for user {user_identifier}")

    # get the user's timezone
    user_tz = ZoneInfo(user_timezone)

    # convert the UTC datetime to the user's timezone
    today_user_tz = today.astimezone(user_tz)

    # format it as "5 April"
    formatted_today = today_user_tz.strftime("%d %B")

    return templates.TemplateResponse(
        request=request,
        name="/notes/today.html",
        context={
            "notes": notes,
            "metrics": metrics,
            "today": formatted_today,
            "range": settings.history_range,
        },
    )


@router.get("/view/{note_id}")
async def read_note(
    request: Request,
    note_id: str,
    user_info: dict = Depends(check_login),
    ai: bool = None,
):
    if ai is None:
        ai = False

    user_identifier = user_info["user_identifier"]
    user_timezone = user_info["timezone"]

    query = Select(Notes).where(
        and_(Notes.user_id == user_identifier, Notes.pkid == note_id)
    )
    note = _safe_record(await db_ops.read_one_record(query=query))

    if note is None:
        logger.warning(f"No note found with ID: {note_id} for user: {user_identifier}")
        return RedirectResponse(url="/notes", status_code=302)

    note = note.to_dict()
    # offset date_created and date_updated to user's timezone
    note["date_created"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=note["date_created"],
        friendly_string=True,
    )
    note["date_updated"] = await date_functions.timezone_update(
        user_timezone=user_timezone,
        date_time=note["date_updated"],
        friendly_string=True,
    )

    logger.info(f"Returning note with ID: {note_id} for user: {user_identifier}")

    return templates.TemplateResponse(
        request=request, name="/notes/view.html", context={"note": note, "ai": ai}
    )
