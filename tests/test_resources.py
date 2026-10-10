# -*- coding: utf-8 -*-
"""
Unit tests for src/resources.py's startup/shutdown hooks on the SQLite file
database: the fail-fast check that Alembic has been applied, the
release_env gate on demo-data seeding, and PRAGMA optimize on shutdown.
Exercised via monkeypatching (settings + db_ops) rather than a real file -
the suite itself always runs on the in-memory driver (see conftest.py).
"""

import asyncio

import pytest
from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult

from src import resources
from src.settings import DatabaseDriverEnum, settings


def test_startup_event_raises_when_sqlite_migrations_missing(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", DatabaseDriverEnum.sqlite.value)

    async def fake_execute_one(query, values=None, return_metadata=False):
        return DatabaseErrorResult({"error": "no such table: alembic_version"})

    monkeypatch.setattr(resources.db_ops, "execute_one", fake_execute_one)

    with pytest.raises(RuntimeError, match="has not been migrated"):
        asyncio.run(resources.startup_event())


def test_startup_event_passes_when_sqlite_migrations_present(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", DatabaseDriverEnum.sqlite.value)
    # Production release_env: demo-data seeding must be skipped entirely.
    monkeypatch.setattr(settings, "release_env", "prd")

    async def fake_execute_one(query, values=None, return_metadata=False):
        return "complete"

    async def fail_read_query(*args, **kwargs):
        raise AssertionError("demo-data check must not run in production")

    monkeypatch.setattr(resources.db_ops, "execute_one", fake_execute_one)
    monkeypatch.setattr(resources.db_ops, "read_query", fail_read_query)

    asyncio.run(resources.startup_event())


def test_shutdown_event_runs_pragma_optimize_on_file_db(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", DatabaseDriverEnum.sqlite.value)
    calls = []

    async def fake_execute_one(query, values=None, return_metadata=False):
        calls.append(str(query))
        return DatabaseErrorResult({"error": "disk I/O error"})

    async def fake_disconnect():
        calls.append("disconnect")

    monkeypatch.setattr(resources.db_ops, "execute_one", fake_execute_one)
    monkeypatch.setattr(resources.async_db, "disconnect", fake_disconnect)

    # A failed optimize is logged, never allowed to block the disconnect.
    asyncio.run(resources.shutdown_event())

    assert calls == ["PRAGMA optimize", "disconnect"]
