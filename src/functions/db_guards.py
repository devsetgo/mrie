# -*- coding: utf-8 -*-
"""
Guards against a quirk in dsg_lib's DatabaseOperations: several of its methods
return a `DatabaseErrorResult` (an error payload) on certain database failures
instead of raising an exception or returning None/[]. A bare `is None`/
`isinstance(x, str)` check misses that case, so every route that calls db_ops
should wrap results in one of these helpers rather than trusting the return
type directly.

Checking `isinstance(result, DatabaseErrorResult)` specifically - not a bare
`isinstance(result, dict)` - matters now that call sites use `execute_one`:
its own successful return (with `return_metadata=True`) is itself a plain
dict (`{"rowcount": ..., "inserted_primary_key": ..., "rows": ...}`), which a
generic dict check would misclassify as an error. This is dsg_lib's own
documented guidance (see `dsg_lib.ai_instructions`), not a guess.

dsg duplicates this pair locally in every endpoint module; mrie is greenfield
here so it's promoted to a single shared module instead.
"""
from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult


def safe_record(result):
    """Return result when it is a genuine ORM object; None otherwise."""
    return (
        result
        if result is not None and not isinstance(result, DatabaseErrorResult)
        else None
    )


def safe_list(result) -> list:
    """Return result when it is a genuine list of ORM objects; empty list otherwise."""
    return result if isinstance(result, list) else []


def is_db_error(result) -> bool:
    """
    True when an `execute_one`/`execute_many` result represents a failure.

    Unlike the deprecated `create_one`/`update_one`/`delete_one` wrappers
    (which returned either an ORM object or a `DatabaseErrorResult`),
    `execute_one` returns the string `"complete"` or a metadata dict on
    success - there's no ORM object to hand back, so `safe_record()` isn't
    the right shape for these call sites. This is just the boolean half.
    """
    return isinstance(result, DatabaseErrorResult)
