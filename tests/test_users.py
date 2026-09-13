# -*- coding: utf-8 -*-
"""
Auth flow coverage for src/endpoints/users.py. The actual WebAuthn ceremony
(login/register *verify* with a real attestation/assertion object) needs a
hardware or virtual authenticator to produce valid signed credentials, which
is out of scope here - so this focuses on what's reachable without one:
registration_open()/registration_authorized() gating (including the
bootstrap token), the dev-login bypass, and the verify endpoints' own
failure paths that happen before any cryptographic verification (missing
session challenge, malformed body, unknown credential).
"""

from types import SimpleNamespace

import pytest
from dsg_lib.async_database_functions.database_operations import DatabaseErrorResult
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)

from src.endpoints import users as users_module
from src.settings import settings

# The global StarletteHTTPException handler (src/app_routes.py) redirects a
# raised HTTPException to an HTML error page (or straight to /users/login
# for 401) unless the request sends this header - the same header the real
# frontend JS (static/js/webauthn.js) sends when calling these endpoints via
# fetch(). Sending it here gets the raw status code + JSON body instead of
# a redirect, both because that's what these endpoints are actually called
# with in production, and because it makes the assertions unambiguous.
JSON_HEADERS = {"Accept": "application/json"}


def test_login_page(client):
    response = client.get("/users/login")
    assert response.status_code == 200


def test_dev_login(client):
    response = client.get("/users/dev-login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/notes"


def test_dev_login_returns_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_fake_login_enabled", False)
    # A raised HTTPException(404) becomes a 303 redirect to /error/404 for a
    # plain browser-style request (no Accept: application/json) - see
    # src/app_routes.py's global exception handler.
    response = client.get("/users/dev-login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/error/404"


def test_logout_clears_session(logged_in_client):
    client = logged_in_client
    response = client.get("/users/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # session_identifier is gone, so a login-gated route bounces to login again
    after = client.get("/notes/", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"] == "/users/login"


def test_register_page_open_with_no_existing_credentials(client):
    # Fresh demo-seeded DB has zero WebAuthnCredentials rows, so registration
    # is open to an anonymous caller - see registration_open() in users.py.
    response = client.get("/users/register", follow_redirects=False)
    assert response.status_code == 200


def test_register_page_open_for_logged_in_admin(logged_in_client):
    response = logged_in_client.get("/users/register", follow_redirects=False)
    assert response.status_code == 200


def test_register_options_requires_bootstrap_token(client):
    response = client.get("/users/register/options", headers=JSON_HEADERS)
    assert response.status_code == 403


def test_register_options_succeeds_with_valid_bootstrap_token(client):
    token = settings.registration_bootstrap_token
    token_value = token.get_secret_value() if token else None
    if not token_value:
        pytest.skip("REGISTRATION_BOOTSTRAP_TOKEN not configured in this environment")

    response = client.get(
        "/users/register/options", headers={"X-Bootstrap-Token": token_value}
    )
    assert response.status_code == 200
    body = response.json()
    assert "challenge" in body


def test_register_options_rejects_wrong_bootstrap_token(client):
    response = client.get(
        "/users/register/options",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": "wrong-token"},
    )
    assert response.status_code == 403


def test_register_options_open_for_logged_in_admin_without_token(logged_in_client):
    # An already-logged-in admin adding another device doesn't need the
    # bootstrap token at all - that's only for the first-ever registration.
    response = logged_in_client.get("/users/register/options")
    assert response.status_code == 200


def test_login_options_returns_challenge(client):
    response = client.get("/users/login/options")
    assert response.status_code == 200
    body = response.json()
    assert "challenge" in body


def test_login_verify_without_challenge_in_session(client):
    response = client.post("/users/login/verify", headers=JSON_HEADERS, content=b"{}")
    assert response.status_code == 400


def test_login_verify_with_malformed_body(client):
    # Establish a pending challenge first so the malformed-JSON check (not
    # the missing-challenge check) is what actually gets exercised.
    client.get("/users/login/options")
    response = client.post(
        "/users/login/verify", headers=JSON_HEADERS, content=b"not-json"
    )
    assert response.status_code == 400


def test_login_verify_with_unknown_credential(client):
    client.get("/users/login/options")
    body = (
        b'{"id": "AAAA", "rawId": "AAAA", "type": "public-key", '
        b'"response": {"clientDataJSON": "AAAA", "authenticatorData": "AAAA", '
        b'"signature": "AAAA"}}'
    )
    response = client.post("/users/login/verify", headers=JSON_HEADERS, content=body)
    assert response.status_code == 401


def test_register_verify_without_challenge_in_session(client):
    token = settings.registration_bootstrap_token
    token_value = token.get_secret_value() if token else None
    if not token_value:
        pytest.skip("REGISTRATION_BOOTSTRAP_TOKEN not configured in this environment")

    response = client.post(
        "/users/register/verify",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": token_value},
        content=b"{}",
    )
    assert response.status_code == 400


def _bootstrap_token_value():
    token = settings.registration_bootstrap_token
    return token.get_secret_value() if token else None


REGISTRATION_CREDENTIAL_BODY = (
    b'{"id": "AAAA", "rawId": "AAAA", "type": "public-key", '
    b'"response": {"clientDataJSON": "AAAA", "attestationObject": "AAAA"}}'
)

AUTHENTICATION_CREDENTIAL_BODY = (
    b'{"id": "AAAA", "rawId": "AAAA", "type": "public-key", '
    b'"response": {"clientDataJSON": "AAAA", "authenticatorData": "AAAA", '
    b'"signature": "AAAA"}}'
)


def test_register_options_denied_when_credential_already_exists(client, monkeypatch):
    # registration_authorized()'s closed-registration guard: even with a
    # correct bootstrap token, a non-admin caller is denied once
    # registration_open() is False (a credential exists somewhere already).
    async def fake_registration_open(request):
        return False

    monkeypatch.setattr(users_module, "registration_open", fake_registration_open)

    response = client.get(
        "/users/register/options",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": "irrelevant"},
    )
    assert response.status_code == 403


def test_register_options_denied_when_bootstrap_token_not_configured(
    client, monkeypatch
):
    # Fails closed (not open) when REGISTRATION_BOOTSTRAP_TOKEN itself isn't
    # set, regardless of what header the caller supplies.
    monkeypatch.setattr(settings, "registration_bootstrap_token", None)

    response = client.get(
        "/users/register/options",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": "anything"},
    )
    assert response.status_code == 403


def test_get_admin_user_raises_when_provisioning_fails(client, monkeypatch):
    # _get_admin_user(): the lookup comes back empty, the provisioning insert
    # itself fails, and the caller gets a clean 500 instead of a None user
    # propagating further.
    async def fake_read_one_record(query):
        return None

    async def fake_execute_one(stmt):
        return DatabaseErrorResult({"error": "boom", "details": "boom"})

    monkeypatch.setattr(users_module.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(users_module.db_ops, "execute_one", fake_execute_one)

    response = client.get(
        "/users/dev-login", headers=JSON_HEADERS, follow_redirects=False
    )
    assert response.status_code == 500


def test_login_verify_fails_cryptographic_verification(client, monkeypatch):
    fake_cred = SimpleNamespace(
        pkid="cred-1", user_id="user-1", public_key=b"pub", sign_count=0
    )

    async def fake_read_one_record(query):
        return fake_cred

    def fake_verify_authentication_response(**kwargs):
        raise InvalidAuthenticationResponse("bad signature")

    monkeypatch.setattr(users_module.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(
        users_module.webauthn,
        "verify_authentication_response",
        fake_verify_authentication_response,
    )

    client.get("/users/login/options")
    response = client.post(
        "/users/login/verify",
        headers=JSON_HEADERS,
        content=AUTHENTICATION_CREDENTIAL_BODY,
    )
    assert response.status_code == 401


def test_login_verify_rejects_non_admin_user(client, monkeypatch):
    calls = {"n": 0}

    async def fake_read_one_record(query):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(
                pkid="cred-1", user_id="user-1", public_key=b"pub", sign_count=0
            )
        return SimpleNamespace(pkid="user-1", user_name="someone-else")

    def fake_verify_authentication_response(**kwargs):
        return SimpleNamespace(new_sign_count=1)

    monkeypatch.setattr(users_module.db_ops, "read_one_record", fake_read_one_record)
    monkeypatch.setattr(
        users_module.webauthn,
        "verify_authentication_response",
        fake_verify_authentication_response,
    )

    client.get("/users/login/options")
    response = client.post(
        "/users/login/verify",
        headers=JSON_HEADERS,
        content=AUTHENTICATION_CREDENTIAL_BODY,
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_register_page_redirects_when_registration_closed(client, monkeypatch):
    async def fake_registration_open(request):
        return False

    monkeypatch.setattr(users_module, "registration_open", fake_registration_open)

    response = client.get("/users/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?login_error=registration_closed"


def test_register_verify_requires_bootstrap_token(client):
    response = client.post(
        "/users/register/verify", headers=JSON_HEADERS, content=b"{}"
    )
    assert response.status_code == 403


def test_register_verify_with_malformed_body(client):
    token_value = _bootstrap_token_value()
    if not token_value:
        pytest.skip("REGISTRATION_BOOTSTRAP_TOKEN not configured in this environment")

    client.get("/users/register/options", headers={"X-Bootstrap-Token": token_value})
    response = client.post(
        "/users/register/verify",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": token_value},
        content=b"not-json",
    )
    assert response.status_code == 400


def test_register_verify_with_invalid_registration_response(client, monkeypatch):
    token_value = _bootstrap_token_value()
    if not token_value:
        pytest.skip("REGISTRATION_BOOTSTRAP_TOKEN not configured in this environment")

    client.get("/users/register/options", headers={"X-Bootstrap-Token": token_value})

    def fake_verify_registration_response(**kwargs):
        raise InvalidRegistrationResponse("bad attestation")

    monkeypatch.setattr(
        users_module.webauthn,
        "verify_registration_response",
        fake_verify_registration_response,
    )

    response = client.post(
        "/users/register/verify",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": token_value},
        content=REGISTRATION_CREDENTIAL_BODY,
    )
    assert response.status_code == 400


def test_register_verify_fails_to_store_credential(client, monkeypatch):
    token_value = _bootstrap_token_value()
    if not token_value:
        pytest.skip("REGISTRATION_BOOTSTRAP_TOKEN not configured in this environment")

    client.get("/users/register/options", headers={"X-Bootstrap-Token": token_value})

    def fake_verify_registration_response(**kwargs):
        return SimpleNamespace(
            credential_id=b"cred-id", credential_public_key=b"pub-key", sign_count=0
        )

    async def fake_execute_one(stmt):
        return DatabaseErrorResult({"error": "boom", "details": "boom"})

    monkeypatch.setattr(
        users_module.webauthn,
        "verify_registration_response",
        fake_verify_registration_response,
    )
    monkeypatch.setattr(users_module.db_ops, "execute_one", fake_execute_one)

    response = client.post(
        "/users/register/verify",
        headers={**JSON_HEADERS, "X-Bootstrap-Token": token_value},
        content=REGISTRATION_CREDENTIAL_BODY,
    )
    assert response.status_code == 500
