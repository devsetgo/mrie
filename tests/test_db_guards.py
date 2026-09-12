"""
Unit tests for src/functions/db_guards.py - pure functions, no database
access needed. These pin down the exact contract every db_ops call site in
the app relies on (see the create_one -> execute_one migration notes in
CLAUDE.md / PROJECT_STATUS.md for why this distinction matters).
"""
from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult

from src.functions.db_guards import is_db_error, safe_list, safe_record


class _FakeRecord:
    pass


def test_safe_record_passes_through_a_real_object():
    record = _FakeRecord()
    assert safe_record(record) is record


def test_safe_record_returns_none_for_none():
    assert safe_record(None) is None


def test_safe_record_returns_none_for_db_error_result():
    error = DatabaseErrorResult({"error": "boom", "details": "details"})
    assert safe_record(error) is None


def test_safe_list_passes_through_a_real_list():
    records = [_FakeRecord(), _FakeRecord()]
    assert safe_list(records) == records


def test_safe_list_returns_empty_list_for_db_error_result():
    error = DatabaseErrorResult({"error": "boom", "details": "details"})
    assert safe_list(error) == []


def test_safe_list_returns_empty_list_for_none():
    assert safe_list(None) == []


def test_is_db_error_true_for_db_error_result():
    assert is_db_error(DatabaseErrorResult({"error": "boom"})) is True


def test_is_db_error_false_for_execute_one_success_values():
    # execute_one()'s own successful returns - a plain "complete" string, or
    # a metadata dict when return_metadata=True - must NOT be mistaken for
    # errors just because the metadata dict happens to also be a dict.
    assert is_db_error("complete") is False
    assert is_db_error({"rowcount": 1, "inserted_primary_key": ("abc",)}) is False


def test_is_db_error_false_for_other_falsy_values():
    assert is_db_error(None) is False
    assert is_db_error([]) is False
