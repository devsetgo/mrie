# -*- coding: utf-8 -*-
"""
Unit tests for src/functions/notes_metrics.py's Postgres-only code paths.

NoteMetricsScalars (src/db_tables.py) is defined unconditionally regardless
of driver - it lives on its own isolated declarative base, so its mere
existence can't affect SQLite's create_tables() - which means these paths
can be exercised here by monkeypatching settings.db_driver and db_ops's
methods, without a live Postgres connection. Only the actual SQL this
config drives (the migration's CREATE MATERIALIZED VIEW, and REFRESH
MATERIALIZED VIEW itself) still needs a real Postgres to verify - see
PROJECT_STATUS.md's documented gap on that front.

pytest-asyncio isn't a dependency of this project, so the async tests below
wrap their bodies in asyncio.run() from an ordinary sync test function
rather than using `async def test_...` directly (which no plugin here
would ever await).
"""

import asyncio

from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult

from src.db_tables import NOTE_METRICS_SCALAR_FIELDS
from src.functions import notes_metrics
from src.functions.notes_metrics import _merge_scalar_metrics
from src.settings import settings


class _FakeRecord:
    def __init__(self, data, pkid="fake-pkid"):
        self._data = data
        self.pkid = pkid

    def to_dict(self):
        return dict(self._data)


def test_merge_scalar_metrics_uses_scalars_when_present():
    result = {"pkid": "abc", "metrics": {"some": "blob"}}
    scalars = dict.fromkeys(NOTE_METRICS_SCALAR_FIELDS, 7)

    merged = _merge_scalar_metrics(result, scalars)

    assert merged is result
    assert merged["pkid"] == "abc"
    for field in NOTE_METRICS_SCALAR_FIELDS:
        assert merged[field] == 7


def test_merge_scalar_metrics_falls_back_to_zero_shape_when_scalars_is_none():
    result = {"pkid": "abc", "metrics": {}}

    merged = _merge_scalar_metrics(result, None)

    for field in NOTE_METRICS_SCALAR_FIELDS:
        expected = {} if field == "mood_metric" else 0
        assert merged[field] == expected


def test_merge_scalar_metrics_fallback_covers_every_declared_field():
    # Guards against the exact drift this helper exists to prevent: if a
    # field is ever added to NOTE_METRICS_SCALAR_FIELDS without updating
    # the fallback shape, this would catch the missing key.
    merged = _merge_scalar_metrics({}, None)
    assert set(merged.keys()) == set(NOTE_METRICS_SCALAR_FIELDS)


def test_update_notes_metrics_postgres_branch_refreshes_view(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    calls = []

    async def fake_read_one_record(query):
        return None  # forces the insert (not update) branch

    async def fake_read_query(query):
        return []  # empty note list keeps _compute_metrics_bundle trivial

    async def fake_execute_one(query, values=None, return_metadata=False):
        calls.append(str(query))
        return "complete"

    monkeypatch.setattr(notes_metrics.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(notes_metrics.db_ops, "read_query", fake_read_query)
    monkeypatch.setattr(notes_metrics.db_ops, "execute_one", fake_execute_one)

    asyncio.run(notes_metrics.update_notes_metrics(user_id="some-user"))

    # Both the NoteMetrics write and the view refresh should have run.
    assert any("INSERT INTO note_metrics" in c for c in calls)
    assert any("REFRESH MATERIALIZED VIEW" in c for c in calls)


def test_update_notes_metrics_postgres_branch_logs_refresh_failure(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    async def fake_read_one_record(query):
        return None

    async def fake_read_query(query):
        return []

    async def fake_execute_one(query, values=None, return_metadata=False):
        if "REFRESH MATERIALIZED VIEW" in str(query):
            return DatabaseErrorResult({"error": "lock timeout"})
        return "complete"

    logged_errors = []
    # loguru's logger doesn't route through stdlib logging (no caplog
    # bridge configured in this project), so patch its .error directly
    # rather than asserting against pytest's caplog fixture.
    monkeypatch.setattr(
        notes_metrics.logger,
        "error",
        lambda *args, **kwargs: logged_errors.append(args),
    )
    monkeypatch.setattr(notes_metrics.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(notes_metrics.db_ops, "read_query", fake_read_query)
    monkeypatch.setattr(notes_metrics.db_ops, "execute_one", fake_execute_one)

    asyncio.run(notes_metrics.update_notes_metrics(user_id="some-user"))

    assert any(
        "Failed to refresh note_metrics_scalars" in args[0] for args in logged_errors
    )


def test_get_or_refresh_postgres_branch_merges_scalars(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    note_metrics_record = _FakeRecord({"pkid": "abc", "metrics": {"x": 1}})
    scalars_record = _FakeRecord(dict.fromkeys(NOTE_METRICS_SCALAR_FIELDS, 5))

    async def fake_read_one_record(query):
        sql = str(query)
        if "note_metrics_scalars" in sql:
            return scalars_record
        return note_metrics_record

    monkeypatch.setattr(notes_metrics.db_ops, "read_one_record", fake_read_one_record)

    result = asyncio.run(notes_metrics.get_or_refresh(user_id="some-user"))

    assert result["pkid"] == "abc"
    for field in NOTE_METRICS_SCALAR_FIELDS:
        assert result[field] == 5


def test_get_or_refresh_postgres_branch_falls_back_when_scalars_missing(monkeypatch):
    monkeypatch.setattr(settings, "db_driver", "postgresql+asyncpg")

    note_metrics_record = _FakeRecord({"pkid": "abc", "metrics": {}})

    async def fake_read_one_record(query):
        sql = str(query)
        if "note_metrics_scalars" in sql:
            return None  # zero-note user: no row in the view yet
        return note_metrics_record

    monkeypatch.setattr(notes_metrics.db_ops, "read_one_record", fake_read_one_record)

    result = asyncio.run(notes_metrics.get_or_refresh(user_id="some-user"))

    for field in NOTE_METRICS_SCALAR_FIELDS:
        expected = {} if field == "mood_metric" else 0
        assert result[field] == expected
