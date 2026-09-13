# -*- coding: utf-8 -*-
"""
Coverage for src/functions/login_required.py's session-validation branches
that check_login's own HTTP-level tests (in test_users.py) don't reach:
- check_user_identifier's "session references a deleted/missing user" branch
  (a real HTTP scenario, exercised via the live app + a monkeypatched DB
  read - the in-memory dev DB reset gotcha CLAUDE.md documents).
- check_session_expiry's "expired" branch (also via the live app, forcing
  expiry by monkeypatching settings.max_age rather than waiting out the
  real window).
- check_session_expiry's "malformed exp value" branch, which has no HTTP
  path to reach at all (a session's exp is always set to a valid float
  timestamp by the login code) - tested by calling the function directly
  with a minimal stand-in request object.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.functions import login_required as login_required_module
from src.functions.login_required import check_session_expiry
from src.settings import settings

JSON_HEADERS = {"Accept": "application/json"}


def test_check_user_identifier_rejects_when_session_user_no_longer_exists(
    logged_in_client, monkeypatch
):
    # Simulates the documented in-memory-SQLite-reset gotcha: the session
    # cookie still names a user_identifier, but that row is gone from the
    # (fresh) database - read_one_record comes back empty rather than
    # raising, so this must fail closed instead of silently treating the
    # missing user as authorized.
    async def fake_read_one_record(query):
        return None

    monkeypatch.setattr(
        login_required_module.db_ops, "read_one_record", fake_read_one_record
    )

    response = logged_in_client.get(
        "/notes/", headers=JSON_HEADERS, follow_redirects=False
    )
    assert response.status_code == 401


def test_check_session_expiry_rejects_expired_session(logged_in_client, monkeypatch):
    # check_session_expiry compares (now - max_age) against the session's
    # already-stored exp timestamp - a large negative max_age pushes that
    # cutoff far into the future, so the stored exp reads as "in the past"
    # without needing to wait out the real expiry window.
    monkeypatch.setattr(settings, "max_age", -999999)

    response = logged_in_client.get(
        "/notes/", headers=JSON_HEADERS, follow_redirects=False
    )
    assert response.status_code == 401


def test_check_session_expiry_rejects_malformed_exp_value():
    # No HTTP path sets a non-numeric/NaN exp - login always stores a real
    # float timestamp - so this calls the function directly with a minimal
    # stand-in request to cover the except ValueError branch.
    fake_request = SimpleNamespace(session={"exp": float("nan")})

    with pytest.raises(HTTPException) as exc_info:
        check_session_expiry(fake_request)

    assert exc_info.value.status_code == 401
