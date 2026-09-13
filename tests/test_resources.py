# -*- coding: utf-8 -*-
"""
Unit tests for src/resources.py's Postgres-only startup fail-fast check.
Exercised here via monkeypatching (settings.db_driver + db_ops.execute_one)
rather than a live Postgres connection - see tests/test_notes_metrics.py's
module docstring for why that's a valid substitute for this kind of check.
"""

import asyncio

import pytest
from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult

from src import resources
from src.settings import settings


def test_startup_event_raises_when_postgres_migrations_missing(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    async def fake_execute_one(query, values=None, return_metadata=False):
        return DatabaseErrorResult(
            {"error": 'relation "alembic_version" does not exist'}
        )

    monkeypatch.setattr(resources.db_ops, "execute_one", fake_execute_one)

    with pytest.raises(RuntimeError, match="has not been migrated"):
        asyncio.run(resources.startup_event())


def test_startup_event_passes_when_postgres_migrations_present(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    async def fake_execute_one(query, values=None, return_metadata=False):
        return "complete"

    monkeypatch.setattr(resources.db_ops, "execute_one", fake_execute_one)

    # Should not raise, and should skip SQLite's create_tables()/demo-data
    # seeding entirely - no further mocking needed for those.
    asyncio.run(resources.startup_event())
