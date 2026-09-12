# -*- coding: utf-8 -*-
import os

from dsg_lib.async_database_functions import database_operations
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError, OperationalError

from src.settings import settings

from .db_init import async_db

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
# no longer sharing dsg's schema, so it's safe for mrie to create its own
# tables on startup. create_all() only creates tables that don't already
# exist, so this is idempotent across restarts.
db_ops = database_operations.DatabaseOperations(async_db)


async def startup_event():
    logger.info("starting up")

    # Under multiple uvicorn workers (e.g. `make run-dev-workers`, against a
    # real file-based SQLite DB rather than the normal per-process in-memory
    # default), every worker process runs this same startup independently and
    # they race here: create_tables() checks "does this table exist yet?"
    # then issues CREATE TABLE as two separate steps, not atomically, so two
    # workers can both see "not yet" and both try to create it - the loser
    # gets `OperationalError: table X already exists`. Left unhandled, that
    # crashes the worker, and uvicorn stops the *entire* server when any
    # worker fails to boot. Since the other worker's table is exactly the
    # schema this one would have created, losing this race is harmless -
    # log it and move on rather than taking the whole app down over it.
    try:
        await async_db.create_tables()
    except OperationalError as ex:
        if "already exists" not in str(ex):
            raise
        logger.debug(f"create_tables() lost a startup race to another worker: {ex}")

    # Dummy demo data is only ever seeded into a private local/in-memory dev
    # DB, never into a real Postgres deployment holding real imported notes.
    if not settings.db_driver.startswith("postgres"):
        from .db_tables import Users

        existing_users = await db_ops.read_query(Select(Users))
        if isinstance(existing_users, list) and len(existing_users) == 0:
            from .functions.demo_data import seed_demo_data

            # Same multi-worker race as create_tables() above: more than one
            # worker can see "users table is empty" before any of them has
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
    await async_db.disconnect()
    return None
