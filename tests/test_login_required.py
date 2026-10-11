# -*- coding: utf-8 -*-
"""
Coverage for src/functions/login_required.py's session-validation branches
that check_login's own HTTP-level tests (in test_users.py) don't reach:
- check_user_identifier's "session references a deleted/missing user" branch
  (a real HTTP scenario, exercised via the live app + a monkeypatched DB
  read - the in-memory dev DB reset gotcha CLAUDE.md documents).
- check_session_expiry's "expired" branch (via the live app, logging in
  with a negative settings.max_age so the stored exp is already past,
  rather than waiting out the real window), plus direct checks that exp is
  an absolute deadline measured in seconds.
- check_session_expiry's "malformed exp value" branch, which has no HTTP
  path to reach at all (a session's exp is always set to a valid float
  timestamp by the login code) - tested by calling the function directly
  with a minimal stand-in request object.
"""

from datetime import datetime, timedelta
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


def test_check_session_expiry_rejects_expired_session(client, monkeypatch):
    # Login stores exp = now + max_age; a negative max_age makes that
    # deadline already past by the time the next request checks it.
    monkeypatch.setattr(settings, "max_age", -60)
    login = client.get("/users/dev-login", follow_redirects=False)
    assert login.status_code == 303

    response = client.get("/notes/", headers=JSON_HEADERS, follow_redirects=False)
    assert response.status_code == 401


def test_login_sets_exp_max_age_seconds_ahead(client, monkeypatch):
    before = datetime.now()
    client.get("/users/dev-login", follow_redirects=False)
    after = datetime.now()

    captured = {}

    def fake_check(request):
        captured["exp"] = request.session["exp"]

    # Read exp back out of the signed cookie via the next checked request.
    monkeypatch.setattr(login_required_module, "check_session_expiry", fake_check)
    client.get("/notes/", headers=JSON_HEADERS, follow_redirects=False)

    expires_at = datetime.fromtimestamp(captured["exp"])
    assert (
        before + timedelta(seconds=settings.max_age)
        <= expires_at
        <= after + timedelta(seconds=settings.max_age)
    )


def test_check_session_expiry_rejects_exp_in_the_past():
    # Regression: exp used to be compared against (now - max_age minutes),
    # so a session that expired hours ago still passed.
    past = (datetime.now() - timedelta(hours=3)).timestamp()
    fake_request = SimpleNamespace(session={"exp": past, "user_identifier": "u"})

    with pytest.raises(HTTPException) as exc_info:
        check_session_expiry(fake_request)

    assert exc_info.value.status_code == 401


def test_check_session_expiry_accepts_exp_in_the_future():
    future = (datetime.now() + timedelta(minutes=1)).timestamp()
    fake_request = SimpleNamespace(session={"exp": future})

    check_session_expiry(fake_request)


def test_check_session_expiry_rejects_malformed_exp_value():
    # No HTTP path sets a non-numeric/NaN exp - login always stores a real
    # float timestamp - so this calls the function directly with a minimal
    # stand-in request to cover the except ValueError branch.
    fake_request = SimpleNamespace(session={"exp": float("nan")})

    with pytest.raises(HTTPException) as exc_info:
        check_session_expiry(fake_request)

    assert exc_info.value.status_code == 401
