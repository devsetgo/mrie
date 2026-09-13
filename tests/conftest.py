# -*- coding: utf-8 -*-
import os

# Hard safety guard - must run before src.main (and everything it transitively
# imports, including src.settings/src.db_init) is ever imported. Settings are
# read once at first import (functools.lru_cache'd in src/settings.py), and
# pydantic-settings gives real environment variables priority over the local
# .env file, so setting these here guarantees the entire test session runs
# against a private in-memory SQLite database and the dev-login bypass - no
# matter what a developer's own .env happens to have configured (e.g.
# DB_DRIVER=sqlite for `make run-dev-workers`, or a real Postgres URL).
# Tests must never be able to reach a real/shared database.
os.environ["DB_DRIVER"] = "memory"
os.environ["RELEASE_ENV"] = "test"
os.environ["DEV_FAKE_LOGIN_ENABLED"] = "True"
# TestClient talks to "http://testserver" (plain HTTP, no TLS). If
# HTTPS_ONLY is left on (as a developer's local .env may have it, to match
# real deployment), the session cookie comes back marked Secure and never
# gets sent on a later request in the same test - see the documented
# HTTPS_ONLY/plain-HTTP gotcha in CLAUDE.md. Every login-gated test would
# otherwise look logged-in on the first request and unauthenticated on the
# next.
os.environ["HTTPS_ONLY"] = "False"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.settings import DatabaseDriverEnum, settings  # noqa: E402

assert settings.db_driver == DatabaseDriverEnum.memory, (
    "Refusing to run tests: DB_DRIVER did not resolve to the in-memory test "
    "database. Tests must never run against a real/shared database."
)


@pytest.fixture
def client():
    # TestClient(app) without a `with` block never runs FastAPI's lifespan
    # (src.resources.startup_event -> create_tables()), so DB-backed routes
    # would 500/silently no-op against a database with no tables at all.
    # The `with` form runs startup on enter and shutdown on exit.
    #
    # Each use of this fixture gets a brand-new, empty in-memory database:
    # shutdown disposes the SQLAlchemy engine, which closes every connection
    # to the shared-cache `:memory:` database, and SQLite discards it once
    # the last connection closes. The next test's startup then recreates the
    # tables and reseeds demo data from scratch (see
    # src/functions/demo_data.py) - tests never see another test's data, but
    # every test does see ~40 pre-existing demo notes/weblinks, so assertions
    # should look for specific content rather than assume an empty table.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in_client(client):
    """A `client` already authenticated as the single admin user via the
    dev-login bypass (forced on above), so tests for login-gated routes don't
    each need to run the full WebAuthn ceremony."""
    response = client.get("/users/dev-login", follow_redirects=False)
    assert response.status_code == 303
    return client
