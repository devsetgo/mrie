# -*- coding: utf-8 -*-
"""
Seeds a private local/in-memory database with dummy Notes/Links data for
local development. Only ever invoked from resources.py's startup_event,
which gates this behind `not settings.db_driver.startswith("postgres")` -
it must never run against the shared production Postgres database dsg owns.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import insert

from ..db_tables import (
    Categories,
    Notes,
    Users,
    WebLinks,
    compute_note_derived_fields,
    compute_weblink_ai_fix,
)
from ..functions.db_guards import is_db_error
from ..functions.encrypt import encrypt_text
from ..resources import db_ops
from ..settings import settings

try:
    import silly
except ImportError:
    silly = None

DEMO_CATEGORIES = ["News", "Programming", "Science", "Technology", "Other"]

# Used in place of `silly` when it's not installed - see requirements/prd.txt
_FALLBACK_WORDS = [
    "quiet", "bright", "steady", "curious", "quick", "calm", "bold", "gentle",
    "restless", "hopeful", "distant", "familiar", "ordinary", "strange",
]


def _demo_paragraph(length: int) -> str:
    if silly:
        return silly.paragraph(length=length)
    return " ".join(random.choice(_FALLBACK_WORDS) for _ in range(length)) + "."


def _demo_adjective() -> str:
    return silly.adjective() if silly else random.choice(_FALLBACK_WORDS)

DEMO_WEB_LINKS = [
    {
        "title": "FastAPI",
        "summary": "A modern, fast web framework for building APIs with Python.",
        "url": "https://fastapi.tiangolo.com/",
        "category": "Programming",
    },
    {
        "title": "Real Python",
        "summary": "Python tutorials for developers of all skill levels.",
        "url": "https://realpython.com/",
        "category": "Programming",
    },
    {
        "title": "Hacker News",
        "summary": "Social news website focusing on computer science and entrepreneurship.",
        "url": "https://news.ycombinator.com/",
        "category": "Technology",
    },
]


async def seed_demo_data(qty_notes: int = 40) -> None:
    logger.warning("Seeding demo data for local development")

    admin_user_name = (
        settings.admin_user.get_secret_value() if settings.admin_user else "demo-admin"
    )
    admin_pkid = str(uuid.uuid4())
    result = await db_ops.execute_one(
        insert(Users).values(
            pkid=admin_pkid,
            user_name=admin_user_name,
            first_name="Demo",
            last_name="Admin",
            email="demo@example.com",
            my_timezone=settings.default_timezone,
            is_active=True,
            is_admin=True,
            roles={},
        )
    )
    if is_db_error(result):
        # Lost a startup race to another worker (see resources.py::startup_event) -
        # devsetgo-lib's execute_one() catches the UNIQUE-constraint violation on
        # user_name internally and returns a DatabaseErrorResult rather than
        # raising, so this is the only place that can actually detect it.
        # Another worker's admin user already exists; skip seeding the rest of
        # the demo set too, so a losing worker doesn't create a duplicate batch
        # of categories/notes/weblinks on top of the winner's.
        logger.info(
            "Demo admin user already exists (created by another worker) - "
            "skipping the rest of demo data seeding"
        )
        return
    logger.info(f"Created demo admin user: {admin_user_name}")

    for name in DEMO_CATEGORIES:
        await db_ops.execute_one(
            insert(Categories).values(
                pkid=str(uuid.uuid4()),
                name=name,
                description=f"{name} related items",
                is_system=True,
                is_post=True,
                is_weblink=True,
            )
        )

    moods = ["positive", "neutral", "negative"]
    mood_analysis_choices = [m[0] for m in settings.mood_analysis_weights]
    for _ in range(qty_notes):
        note_text = _demo_paragraph(length=random.randint(5, 20))
        summary_text = note_text[:50]
        days_ago = random.randint(0, 365 * 2)
        date_created = (
            datetime.now(timezone.utc) - timedelta(days=days_ago)
        ).replace(tzinfo=None)
        mood = random.choice(moods)
        mood_analysis = random.choice(mood_analysis_choices)
        tags = list({_demo_adjective() for _ in range(random.randint(1, 3))})
        # Notes.note/summary are Python properties that encrypt on assignment
        # (see db_tables.py) - they only apply via ORM-instance construction
        # (Notes(note=...)), not a Core-level insert().values(), which only
        # recognizes real mapped columns (_note/_summary). Encrypt explicitly.
        # Same reasoning for word_count/character_count/ai_fix/demo_created -
        # normally computed by the before_insert ORM event, which a Core
        # insert() bypasses entirely - see compute_note_derived_fields().
        derived = compute_note_derived_fields(
            note=note_text,
            mood=mood,
            mood_analysis=mood_analysis,
            tags=tags,
            demo_created=1,
        )
        await db_ops.execute_one(
            insert(Notes).values(
                pkid=str(uuid.uuid4()),
                mood=mood,
                _note=encrypt_text(note_text),
                _summary=encrypt_text(summary_text),
                tags=tags,
                mood_analysis=mood_analysis,
                user_id=admin_pkid,
                date_created=date_created,
                date_updated=date_created,
                **derived,
            )
        )

    for item in DEMO_WEB_LINKS:
        await db_ops.execute_one(
            insert(WebLinks).values(
                pkid=str(uuid.uuid4()),
                title=item["title"],
                summary=item["summary"],
                url=item["url"],
                category=item["category"],
                user_id=admin_pkid,
                # Same before_insert-bypass reasoning as Notes above - see
                # compute_weblink_ai_fix() in db_tables.py.
                ai_fix=compute_weblink_ai_fix(
                    image_preview_data=None, title=item["title"], summary=item["summary"]
                ),
            )
        )

    logger.info(f"Seeded {qty_notes} demo notes and {len(DEMO_WEB_LINKS)} demo weblinks")
