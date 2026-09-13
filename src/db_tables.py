# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM models for mrie's Notes + Links (WebLinks) feature.

mrie owns its own private database - this is no longer a shared schema with
devsetgo.com (dsg). Table structure originated as a port of dsg's
src/db_tables.py (kept for continuity while notes are imported from dsg's
export), but mrie is free to evolve its own schema independently from here
on. mrie creates its own tables on startup (see resources.py); there is no
Alembic migration setup yet - add one if/when schema changes need more care
than create_all()'s "create missing tables" behavior provides.
"""

import re

from dsg_lib.async_database_functions import base_schema
from loguru import logger
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    event,
    func,
)
from sqlalchemy.orm import class_mapper, relationship

from .db_init import async_db
from .functions.encrypt import (
    DecryptionError,
    EncryptionError,
    decrypt_text,
    encrypt_text,
)
from .settings import settings

if settings.db_driver.startswith("sqlite"):
    schema_base = base_schema.SchemaBaseSQLite
elif settings.db_driver.startswith("postgres"):
    schema_base = base_schema.SchemaBasePostgres
else:
    raise ValueError("Untested database driver")


class Users(schema_base, async_db.Base):
    __tablename__ = "users"  # Name of the table in the database
    __tableargs__ = {"comment": "Users of the application"}

    first_name = Column(String, unique=False, index=True)
    last_name = Column(String, unique=False, index=True)
    user_name = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=False, index=True, nullable=True)
    provider = Column(String, unique=False)
    my_timezone = Column(String, unique=False, index=True, default="America/New_York")
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    update_by = Column(String, unique=False, index=True)
    date_last_login = Column(DateTime, unique=False, index=True)
    is_locked = Column(Boolean, default=False, index=True, nullable=False)
    roles = Column(JSON, default={})  # Roles of the user
    removal_flag = Column(DateTime, index=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }

    # Only relationships to tables mrie actually maps. dsg's Users model also
    # relates to Posts, but mrie has no Posts model - leaving that
    # relationship in would break mapper configuration (dangling class name).
    web_links = relationship("WebLinks", back_populates="users", cascade="all,delete")
    notes = relationship("Notes", back_populates="users", cascade="all,delete")
    note_metrics = relationship(
        "NoteMetrics", back_populates="users", cascade="all,delete"
    )
    notifications = relationship(
        "Notifications", back_populates="users", cascade="all,delete"
    )
    webauthn_credentials = relationship(
        "WebAuthnCredentials", back_populates="users", cascade="all,delete"
    )


class WebAuthnCredentials(schema_base, async_db.Base):
    __tablename__ = "webauthn_credentials"
    __tableargs__ = {"comment": "Registered passkeys for login"}

    user_id = Column(String, ForeignKey("users.pkid"), nullable=False, index=True)
    credential_id = Column(String, unique=True, index=True, nullable=False)
    public_key = Column(LargeBinary, nullable=False)
    sign_count = Column(Integer, default=0, nullable=False)
    transports = Column(JSON, default=list)
    device_name = Column(String, nullable=True)
    date_last_used = Column(DateTime, nullable=True)

    users = relationship("Users", back_populates="webauthn_credentials")

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }


class WebLinks(schema_base, async_db.Base):
    __tablename__ = "web_links"  # Name of the table in the database
    __tableargs__ = {"comment": "Interesting things that the user finds"}

    title = Column(String, unique=False, index=True)
    summary = Column(String, unique=False, index=True)
    comment = Column(String, unique=False, index=True)
    url = Column(String, unique=False, index=True)
    category = Column(String, unique=False, index=True)
    public = Column(Boolean, default=False)
    image_preview_data = Column(
        LargeBinary,
        nullable=True,
        comment="Binary data of the image preview of the webpage",
    )
    ai_fix = Column(Boolean, default=False)

    user_id = Column(String, ForeignKey("users.pkid"))
    users = relationship("Users", back_populates="web_links")

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }

    @property
    def is_youtube(self) -> bool:
        if not self.url:
            return False
        url_lower = self.url.lower()
        return "youtube.com" in url_lower or "youtu.be" in url_lower

    def update_ai_fix(self):
        self.ai_fix = compute_weblink_ai_fix(
            image_preview_data=self.image_preview_data,
            title=self.title,
            summary=self.summary,
        )


def compute_weblink_ai_fix(*, image_preview_data, title, summary) -> bool:
    """
    Same rule as `WebLinks.update_ai_fix()`/the before_insert/before_update
    listeners below, extracted so it can also be called explicitly wherever
    a WebLinks row is created/updated via `db_ops.execute_one()` with a
    Core-level `insert()`/`update()` statement - those bypass SQLAlchemy's
    ORM mapper events entirely (they only fire for `session.add()`/flush),
    so the listeners below silently would not run for that path.
    """
    return image_preview_data is None or title is None or summary is None


@event.listens_for(WebLinks, "before_insert")
def before_insert_listener(mapper, connection, target):
    target.update_ai_fix()


@event.listens_for(WebLinks, "before_update")
def before_update_listener(mapper, connection, target):
    target.update_ai_fix()


class Categories(schema_base, async_db.Base):
    __tablename__ = "categories"  # Name of the table in the database
    __tableargs__ = {"comment": "Categories of interesting things"}

    name = Column(String(50), unique=False, index=True)
    description = Column(String(500), unique=False, index=True)
    is_post = Column(Boolean, default=False, index=True)
    is_weblink = Column(Boolean, default=False, index=True)
    is_system = Column(Boolean, default=True, index=True)

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }


# TODO: Recreate as a view
class NoteMetrics(schema_base, async_db.Base):
    __tablename__ = "note_metrics"

    user_id = Column(
        String, ForeignKey("users.pkid"), nullable=False, index=True, unique=True
    )
    word_count = Column(Integer, default=0)
    character_count = Column(Integer, default=0)
    note_count = Column(Integer, default=0)
    mood_metric = Column(JSON)
    metrics = Column(JSON)
    ai_fix_count = Column(Integer, default=0)
    total_unique_tag_count = Column(Integer, default=0)
    users = relationship("Users", back_populates="note_metrics")

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }


class Notes(schema_base, async_db.Base):
    __tablename__ = "notes"
    __tableargs__ = {"comment": "Notes that the user writes"}

    mood = Column(String(500), unique=False, index=True)
    mood_analysis = Column(String(500), unique=False, index=True)
    _note = Column(LargeBinary, unique=False, nullable=False)
    _summary = Column(LargeBinary, unique=False, index=True)
    tags = Column(JSON)
    word_count = Column(Integer)
    character_count = Column(Integer)
    ai_fix = Column(Boolean, default=False)
    user_id = Column(String, ForeignKey("users.pkid"), nullable=False, index=True)
    users = relationship("Users", back_populates="notes")
    demo_created = Column(Integer, default=0, index=True)

    if settings.db_driver.startswith("postgres"):
        __table_args__ = (
            Index("ix_notes__note_hash", func.md5(_note)),
            {"schema": "public"},
        )
    else:
        __table_args__ = ()

    def to_dict(self):
        data = {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }
        data["note"] = self.note
        data["summary"] = self.summary
        return data

    @property
    def note(self):
        try:
            return decrypt_text(self._note)
        except DecryptionError as e:
            error: str = f"Failed to decrypt note: {e}"
            logger.error(error)
            return error

    @note.setter
    def note(self, value):
        try:
            self._note = encrypt_text(value)
        except EncryptionError as e:
            error: str = f"Failed to encrypt note: {e}"
            logger.error(error)
            return error

    @property
    def summary(self):
        try:
            return decrypt_text(self._summary)
        except DecryptionError as e:
            error: str = f"Failed to decrypt summary: {e}"
            logger.error(error)
            return error

    @summary.setter
    def summary(self, value):
        try:
            self._summary = encrypt_text(value)
        except EncryptionError as e:
            error: str = f"Failed to encrypt summary: {e}"
            logger.error(error)
            return error


def compute_note_derived_fields(
    *, note: str, mood: str, mood_analysis: str, tags: list, demo_created: int = 0
) -> dict:
    """
    Same computation as the before_insert/before_update listener below,
    extracted so it can also be called explicitly wherever a Notes row is
    created/updated via `db_ops.execute_one()` with a Core-level
    `insert()`/`update()` statement - those bypass SQLAlchemy's ORM mapper
    events entirely (they only fire for `session.add()`/flush), so the
    listener below silently would not run for that path. Returns the fields
    to pass into `.values()`: word_count, character_count, ai_fix,
    demo_created (bumped from 1 to 2, same as the listener does, so a demo
    note is only ever flagged for AI review once).
    """
    word_count = len(note.split())
    character_count = len(note)

    pattern = re.compile("[^a-zA-Z, ]")
    ai_fix = False

    if mood not in ["positive", "negative", "neutral"]:
        ai_fix = True

    if " " in mood_analysis:
        ai_fix = True

    for tag in tags:
        if pattern.search(tag) or " " in tag:
            ai_fix = True
            break

    if demo_created == 1:
        ai_fix = True
        demo_created = 2

    return {
        "word_count": word_count,
        "character_count": character_count,
        "ai_fix": ai_fix,
        "demo_created": demo_created,
    }


@event.listens_for(Notes, "before_insert")
@event.listens_for(Notes, "before_update")
def note_on_change(mapper, connection, target):
    derived = compute_note_derived_fields(
        note=target.note,
        mood=target.mood,
        mood_analysis=target.mood_analysis,
        tags=target.tags,
        demo_created=target.demo_created,
    )
    target.word_count = derived["word_count"]
    target.character_count = derived["character_count"]
    target.ai_fix = derived["ai_fix"]
    target.demo_created = derived["demo_created"]


class AboutPage(schema_base, async_db.Base):
    __tablename__ = "about_page"
    __tableargs__ = {"comment": "Singleton public About page content, admin-editable"}

    content = Column(String, nullable=False, default="")

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }


class Notifications(schema_base, async_db.Base):
    __tablename__ = "notifications"
    __tableargs__ = {
        "comment": "User notifications from background tasks and system events"
    }

    user_id = Column(String, ForeignKey("users.pkid"), nullable=False, index=True)
    message = Column(String(500), nullable=False)
    category = Column(String(50), default="info", index=True)  # ai, error, info
    is_read = Column(Boolean, default=False, index=True)
    note_id = Column(String, nullable=True)  # optional link back to a note
    users = relationship("Users", back_populates="notifications")

    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in class_mapper(self.__class__).columns
        }
