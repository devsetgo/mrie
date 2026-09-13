# -*- coding: utf-8 -*-
"""
This module provides functionality for importing notes from a CSV file into a database. It leverages OpenAI for creating summaries and performing analysis on the notes. The main functionality is encapsulated in the `read_notes_from_file` asynchronous function, which reads notes from a specified CSV file and stores them in the database using the user's identifier.

Functions:
    read_notes_from_file(csv_file: list, user_id: str) -> dict: Reads notes from a CSV file and stores them in the database.

Modules:
    csv: For reading from and writing to CSV files.
    itertools: Provides access to the iterator functions.
    datetime: For working with dates and times.
    dateutil.parser: For parsing dates and times from strings.
    dateutil.tz: For timezone definitions.
    loguru: For logging.
    sqlalchemy: For database interaction.
    tqdm: For providing a progress bar.
    tqdm.asyncio: For providing an asynchronous progress bar.
    db_tables: Defines the database tables.
    functions.ai: Contains functions related to AI operations.
    functions.notes_metrics: Contains functions for calculating metrics on notes.
    resources.db_ops: Contains database operations.

Example:
    To use this module to import notes from a CSV file, you would call the `read_notes_from_file` function with the path to your CSV file and the user identifier as arguments.

Author:
    Mike Ryan
    MIT Licensed
"""

import ast
import asyncio
import csv
import itertools
import uuid
from datetime import datetime

from dateutil.parser import parse
from dateutil.tz import UTC
from loguru import logger
from sqlalchemy import Select, insert, update

from ..db_tables import Notes, compute_note_derived_fields
from ..functions import ai, notes_metrics
from ..functions._optional_deps import async_tqdm, tqdm
from ..functions.db_guards import safe_record as _safe_record
from ..functions.encrypt import encrypt_text
from ..resources import db_ops

# Minimal format: a fresh note with no prior AI analysis, gets queued for AI.
SIMPLE_HEADERS = ["my_note", "mood", "date_created"]

# dsg's Admin > Notes Export (/admin/export-notes) CSV format. These notes
# already carry dsg's mood/tags/summary analysis, so importing them here
# should NOT re-run AI - it would waste API calls and could overwrite a
# human-curated mood with a different AI guess.
EXPORT_HEADERS = [
    "PKID",
    "User ID",
    "Mood",
    "Mood Analysis",
    "Note",
    "Summary",
    "Tags",
    "Word Count",
    "Character Count",
    "Date Created",
]


def detect_csv_format(headers) -> str | None:
    if headers == SIMPLE_HEADERS:
        return "simple"
    if headers == EXPORT_HEADERS:
        return "export"
    return None


def parse_tags_field(raw) -> list:
    """Parse a Tags cell that may be a Python list repr (e.g. "['a', 'b']",
    as produced by Jinja's default str() rendering in dsg's export template)
    or a plain comma-separated string.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    raw = raw.strip()
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, (list, tuple)):
            return [str(t).strip() for t in parsed if str(t).strip()]
    except ValueError, SyntaxError:
        pass
    return [t.strip() for t in raw.split(",") if t.strip()]


async def read_notes_from_file(csv_file, user_id: str):
    """
    Reads notes from a CSV file and stores them in the database.

    Accepts two formats: the minimal `my_note`/`mood`/`date_created` format
    (queued for AI analysis after import), or dsg's Notes Export format,
    which already has mood/tags/summary and is imported as-is with no AI call.

    Args:
        csv_file: A csv.DictReader over the uploaded file.
        user_id (str): The user identifier.

    Returns:
        dict: A dictionary containing an error message if the CSV file is invalid.
    """
    print("Beginning processing")
    csv_format = detect_csv_format(csv_file.fieldnames)
    if csv_format is None:
        validation_result = validate_csv_headers(csv_file)
        logger.error(validation_result)
        return {"error": csv_file}

    # Create a copy of the CSV file and count the number of notes
    csv_file, csv_file_copy = itertools.tee(csv_file)
    note_count = sum(1 for _ in csv_file_copy)
    logger.info(f"Number of notes to process: {note_count} (format={csv_format})")

    if csv_format == "export":
        count = 0
        for c in tqdm(
            csv_file, desc="Importing exported notes (no AI)", total=note_count
        ):
            count += 1
            date_created = parse_date(c["Date Created"]) or datetime.utcnow()
            note_text = c.get("Note") or ""
            summary_text = c.get("Summary") or ""
            mood = (c.get("Mood") or "").strip().lower() or "neutral"
            mood_analysis = (c.get("Mood Analysis") or "").strip().lower()
            tags = parse_tags_field(c.get("Tags"))
            # Not forcing ai_fix here - compute_note_derived_fields (the same
            # rule the before_insert listener used to apply) flags it
            # automatically if mood/mood_analysis/tags look off.
            derived = compute_note_derived_fields(
                note=note_text, mood=mood, mood_analysis=mood_analysis, tags=tags
            )
            await db_ops.execute_one(
                insert(Notes).values(
                    pkid=str(uuid.uuid4()),
                    mood=mood,
                    _note=encrypt_text(note_text),
                    _summary=encrypt_text(summary_text),
                    mood_analysis=mood_analysis,
                    tags=tags,
                    date_created=date_created,
                    date_updated=date_created,
                    user_id=user_id,
                    **derived,
                )
            )
        logger.info(
            f"Imported {count} notes from export format, no AI processing queued"
        )
        await notes_metrics.update_notes_metrics(user_id=user_id)
        return

    # Initialize a list to store the IDs of the notes that need AI processing
    ai_ids = []
    count = 0

    # Iterate over each note in the CSV file
    for c in tqdm(csv_file, desc="Importing notes", total=note_count):
        count += 1
        # Parse the date created
        date_created = parse_date(c["date_created"])
        mood = c["mood"]
        logger.info(f"Processing note {count} for {date_created}")
        # If the mood is not one of the expected values, set it to "processing"
        if mood not in ["positive", "negative", "neutral"]:
            mood = "processing"

        note_text = c["my_note"]

        # Initialize the analysis with "processing" values
        analysis = {
            "tags": {"tags": ["processing"]},
            "summary": "processing",
            "mood_analysis": "processing",
        }

        # Create the note. ai_fix is forced True here (rather than computed
        # via compute_note_derived_fields, which could come back False for a
        # note whose mood happens to already look valid) - this note is
        # freshly imported and still pending real AI analysis, so it must
        # show as needing a fix until process_note() below actually runs.
        new_pkid = str(uuid.uuid4())
        derived = compute_note_derived_fields(
            note=note_text,
            mood=mood,
            mood_analysis=analysis["mood_analysis"],
            tags=analysis["tags"]["tags"],
        )
        derived["ai_fix"] = True
        await db_ops.execute_one(
            insert(Notes).values(
                pkid=new_pkid,
                mood=mood,
                _note=encrypt_text(note_text),
                tags=analysis["tags"]["tags"],
                _summary=encrypt_text(analysis["summary"]),
                mood_analysis=analysis["mood_analysis"],
                date_created=date_created,
                date_updated=date_created,
                user_id=user_id,
                **derived,
            )
        )
        logger.info(f"Created note with ID: {new_pkid}")
        # Add the ID of the note to the list of IDs for AI processing
        ai_ids.append(new_pkid)

    logger.info(f"Notes imoorted: {count}")
    # Process the notes with AI
    await process_ai(list_of_ids=ai_ids, user_identifier=user_id)
    # Update the notes metrics
    await notes_metrics.update_notes_metrics(user_id=user_id)


def parse_date(date_created):
    """
    Parses a date string into a datetime object.

    Args:
        date_created (str): The date string to parse.

    Returns:
        datetime: The parsed date, or None if the date string couldn't be parsed.
    """

    try:
        # Try to parse the date with time
        dt = datetime.strptime(date_created, "%m/%d/%Y %H:%M")
    except ValueError:
        try:
            # If that fails, try to parse the date without time
            dt = datetime.strptime(date_created, "%m/%d/%Y")
        except ValueError:
            try:
                # If that also fails, try to parse the date with dateutil.parser.parse
                dt = parse(date_created)
            except ValueError:
                # If all attempts fail, return None
                return None

    # If the datetime object is offset-aware, convert it to UTC and make it offset-naive
    if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)

    return dt


async def process_note(
    note_id: str, semaphore: asyncio.Semaphore, user_identifier: str
):
    async with semaphore:
        try:
            query = Select(Notes).where(Notes.pkid == note_id)
            note = _safe_record(await db_ops.read_one_record(query=query))
            note = note.to_dict()
            logger.debug(f"AI Processing of note: {note}")

            # Single AI call — get_analysis now always returns mood too
            analysis = await ai.get_analysis(content=note["note"])
            logger.info(f"Received analysis from AI: {analysis}")

            # Honour valid user-selected mood; use AI-derived mood only if needed
            stored_mood = note.get("mood", "")
            if stored_mood in ("positive", "negative", "neutral"):
                final_mood = stored_mood
            else:
                ai_mood = analysis.get("mood") or {}
                final_mood = (
                    ai_mood.get("mood", "neutral")
                    if isinstance(ai_mood, dict)
                    else "neutral"
                )
                if final_mood not in ("positive", "negative", "neutral"):
                    final_mood = "neutral"

            # ai_fix=False is forced here (not computed via
            # compute_note_derived_fields) - this is the terminal state after
            # AI analysis has actually completed, so it must clear
            # regardless of what the recomputed rule would say.
            note_update = {
                "tags": analysis["tags"]["tags"],
                "_summary": encrypt_text(analysis["summary"]),
                "mood_analysis": analysis["mood_analysis"],
                "ai_fix": False,
                "mood": final_mood,
            }

            await db_ops.execute_one(
                update(Notes).where(Notes.pkid == note["pkid"]).values(**note_update)
            )
            logger.info(f"Resubmitted note to AI with ID: {note['pkid']}")
        except Exception as e:
            logger.error(f"Error processing note ID {note_id}: {e}")


async def process_ai(list_of_ids: list, user_identifier: str):
    semaphore = asyncio.Semaphore(20)  # Limit to 20 concurrent tasks
    tasks = [
        process_note(note_id, semaphore, user_identifier) for note_id in list_of_ids
    ]
    for chunk in async_tqdm(
        [tasks[i : i + 20] for i in range(0, len(tasks), 20)], desc="AI processing"
    ):
        await asyncio.gather(*chunk)


def validate_csv_headers(csv_reader: csv.DictReader):
    """
    Validates the headers of a CSV file.

    Args:
        csv_reader (csv.DictReader): The CSV reader object.

    Returns:
        dict: A dictionary containing the status of the validation. If the validation fails,
              the dictionary also contains the missing and extra headers.
    """

    # Get the actual headers from the CSV file
    headers = csv_reader.fieldnames
    logger.debug(f"Headers: {headers}")

    if detect_csv_format(headers) is not None:
        return {"status": "success"}

    # Neither accepted format matched - report against the simple format
    # (the common case) but mention both in the error for clarity.
    missing_headers = [
        header for header in SIMPLE_HEADERS if header not in (headers or [])
    ]
    extra_headers = [
        header for header in (headers or []) if header not in SIMPLE_HEADERS
    ]

    data = {
        "status": {
            "error": "Invalid CSV file",
            "missing_headers": missing_headers,
            "extra_headers": extra_headers,
            "accepted_formats": {"simple": SIMPLE_HEADERS, "export": EXPORT_HEADERS},
        }
    }
    logger.error(data)
    return data
