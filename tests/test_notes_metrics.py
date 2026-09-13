# -*- coding: utf-8 -*-
"""
Unit tests for src/functions/notes_metrics.py's dialect-merge helper.
_merge_scalar_metrics() is the one piece of get_or_refresh()'s Postgres
branch that's a plain function on plain dicts - the rest of that branch
needs a live Postgres-backed NoteMetricsScalars query (that class is only
defined when settings.is_postgres was true at db_tables.py import time,
which this test suite's memory driver never is - see conftest.py), so it
stays an untested-but-documented gap (see PROJECT_STATUS.md). This at
least pins down the merge/fallback logic itself: a field-name mismatch
between NOTE_METRICS_SCALAR_FIELDS and whatever a real NoteMetricsScalars
row provides would surface here.
"""

from src.db_tables import NOTE_METRICS_SCALAR_FIELDS
from src.functions.notes_metrics import _merge_scalar_metrics


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
