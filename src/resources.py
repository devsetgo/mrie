# -*- coding: utf-8 -*-
import os

from dsg_lib.async_database_functions import database_operations
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import Select, text
from sqlalchemy.exc import IntegrityError

from src.settings import settings

from .db_init import async_db
from .functions.db_guards import is_db_error

templates = Jinja2Templates(directory="templates")
templates.env.globals["plausible_domain"] = settings.plausible_domain
templates.env.globals["plausible_script_src"] = settings.plausible_script_src
# Surfaced as a persistent banner in base_app.html - if the dev fake-login
# bypass is reachable at all, that should never be silently invisible while
# poking around the app.
templates.env.globals["dev_fake_login_allowed"] = settings.dev_fake_login_allowed
# Gates dev-only UI (e.g. the raw-metrics "Data" card on the notes
# dashboard) - true for any non-production release_env, no separate opt-in
# flag needed since there's nothing to bypass, just extra debug output.
templates.env.globals["is_dev_environment"] = settings.is_dev_environment


def static_version(path: str) -> int:
    """
    Returns the mtime of a file under static/, for use as a cache-busting
    query string on its URL (?v=...). Without this, browsers can keep
    serving a stale cached copy of e.g. static/js/webauthn.js after it's
    edited, silently running old logic against a server that's since
    changed - a source of confusing, hard-to-reproduce bugs.
    """
    try:
        return int(os.path.getmtime(os.path.join("static", path)))
    except OSError:
        return 0


templates.env.globals["static_version"] = static_version

# mrie owns its own private database (Notes/Links tables included) - it's
# no longer sharing dsg's schema. Schema is managed by Alembic (see
# alembic/) for the SQLite file database; see startup_event()'s
# create_tables() call below for the one exception (the in-memory test
# driver).
db_ops = database_operations.DatabaseOperations(async_db)


async def startup_event():
    logger.info("starting up")

    if settings.is_memory_db:
        # The in-memory database starts empty on every boot (and every test,
        # see tests/conftest.py), so it has no migration history to follow -
        # create_all() straight from the models is both correct and fastest.
        await async_db.create_tables()
    else:
        # The file database's schema is owned by Alembic (see alembic/),
        # applied out-of-band before the app starts (the Docker image's
        # entrypoint, the devcontainer postCreateCommand, CI, or
        # `make alembic-migrate`) - never from app boot, where several
        # uvicorn workers would race to apply the same migration. If that
        # step was skipped, the app would otherwise boot cleanly and only
        # fail later, per-request - either as a confusing OperationalError,
        # or worse, silently as zeroed-out data, since several call sites
        # swallow a `DatabaseErrorResult` into an empty/default value (see
        # db_guards.py). Fail loudly here instead.
        migration_check = await db_ops.execute_one(
            text("SELECT 1 FROM alembic_version LIMIT 1")
        )
        if is_db_error(migration_check):
            raise RuntimeError(
                "SQLite schema has not been migrated - the 'alembic_version' "
                "table is missing or unreadable. Run `make alembic-migrate` "
                "(`alembic upgrade head`) before starting the app against "
                "this database - `make run-dev` and the Docker image do this "
                "automatically."
            )

    # Dummy demo data is only ever seeded into a dev/test database, never
    # into production's real imported notes. Gated on release_env's explicit
    # dev allowlist rather than the driver, since production is SQLite too.
    if settings.is_dev_environment:
        from .db_tables import Users

        existing_users = await db_ops.read_query(Select(Users))
        if isinstance(existing_users, list) and len(existing_users) == 0:
            from .functions.demo_data import seed_demo_data

            # Under multiple uvicorn workers against the same file, more than
            # one worker can see "users table is empty" before any of them has
            # finished inserting the demo admin user. The actual handling for
            # this lives inside seed_demo_data() itself (via safe_record() on
            # the create_one() result) - devsetgo-lib's create_one() catches
            # the UNIQUE-constraint violation internally and returns a
            # DatabaseErrorResult rather than raising, so it never reaches an
            # except clause here at all. This except is a defensive fallback
            # only, in case that ever changes back to raising.
            try:
                await seed_demo_data()
            except IntegrityError as ex:
                logger.debug(
                    f"seed_demo_data() lost a startup race to another worker: {ex}"
                )

    return None


async def shutdown_event():
    logger.info("shutting down")
    if not settings.is_memory_db:
        # Refreshes the query planner's statistics for any table whose
        # query patterns changed since the last run - SQLite's recommended
        # way to keep plans good on a long-lived database, and a no-op
        # (milliseconds) when nothing needs it.
        result = await db_ops.execute_one(text("PRAGMA optimize"))
        if is_db_error(result):
            logger.warning(f"PRAGMA optimize failed on shutdown: {result}")
    await async_db.disconnect()
    return None
