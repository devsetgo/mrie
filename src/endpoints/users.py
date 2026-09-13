# -*- coding: utf-8 -*-
"""
Passkey (WebAuthn) login/registration/logout for mrie.

mrie is single-user: only the identity configured as `settings.admin_user`
may ever hold a login session. There is a bootstrap problem on a brand-new
database (or a fresh demo-seeded dev one): nothing can create a passkey
credential except a successful registration, but registration must stay
closed to the public once a credential exists. `registration_open()`
resolves the "is there anything to register at all" question: open when
either (a) the caller is already logged in as the admin (adding another
device), or (b) zero `WebAuthnCredentials` rows exist anywhere yet
(first-ever registration). Once any credential exists and nobody is logged
in, path (b) is permanently closed. Registration always provisions/uses the
single `settings.admin_user` identity - it never accepts a caller-supplied
username, so this can never create a second or rogue account.

`registration_open()` alone isn't enough for path (b): anyone who discovers
the URL before the real owner registers could otherwise claim the one
identity permanently. `registration_authorized()` closes that: the
already-logged-in-admin path is unchanged, but the first-ever-registration
path additionally requires the caller to present `REGISTRATION_BOOTSTRAP_TOKEN`
(via the `X-Bootstrap-Token` header) matching `settings.registration_bootstrap_token`.
If that setting isn't configured, path (b) is closed to everyone - fails
closed rather than open.

Author:
    Mike Ryan
    MIT Licensed
"""

import secrets
import uuid
from datetime import datetime, timedelta

import webauthn
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from loguru import logger
from sqlalchemy import Select, insert, update
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..db_tables import Users, WebAuthnCredentials
from ..functions.db_guards import is_db_error, safe_list, safe_record
from ..resources import db_ops, templates
from ..settings import settings

router = APIRouter()

CHALLENGE_SESSION_KEY = "webauthn_challenge"


async def registration_open(request: Request) -> bool:
    if request.session.get("is_admin") is True:
        return True
    cred_count = await db_ops.count_query(Select(WebAuthnCredentials))
    return isinstance(cred_count, int) and cred_count == 0


async def registration_authorized(request: Request) -> bool:
    if request.session.get("is_admin") is True:
        logger.debug("registration_authorized: allowed via existing admin session")
        return True
    if not await registration_open(request):
        logger.debug("registration_authorized: denied, a credential already exists")
        return False

    expected = settings.registration_bootstrap_token
    expected_value = expected.get_secret_value() if expected else ""
    if not expected_value:
        logger.warning(
            "Passkey registration attempted but REGISTRATION_BOOTSTRAP_TOKEN is not configured"
        )
        return False

    supplied = request.headers.get("x-bootstrap-token")
    logger.debug(
        f"registration_authorized: token header present={supplied is not None} "
        f"length={len(supplied) if supplied else 0} (expected length={len(expected_value)})"
    )
    matched = supplied is not None and secrets.compare_digest(supplied, expected_value)
    logger.debug(f"registration_authorized: token match={matched}")
    return matched


async def _get_admin_user() -> Users:
    admin_name = settings.admin_user.get_secret_value()
    user_query = Select(Users).where(Users.user_name == admin_name)
    user = safe_record(await db_ops.read_one_record(user_query))
    if user is None:
        result = await db_ops.execute_one(
            insert(Users).values(
                pkid=str(uuid.uuid4()),
                user_name=admin_name,
                my_timezone=settings.default_timezone,
                is_active=True,
                is_admin=True,
                roles={},
            )
        )
        if not is_db_error(result):
            # execute_one() doesn't hand back the inserted row itself (unlike
            # the deprecated create_one() it replaces) - re-read by the
            # user_name we just inserted, same query as the lookup above.
            user = safe_record(await db_ops.read_one_record(user_query))
    if user is None:
        raise HTTPException(status_code=500, detail="Failed to provision admin user")
    return user


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="users/login.html", context={"request": request}
    )


@router.get("/dev-login")
async def dev_login(request: Request):
    """
    Dev-only login bypass - skips the WebAuthn ceremony entirely. Exists so a
    reset in-memory dev DB (which wipes every registered passkey) doesn't
    force re-registration on every restart. Returns 404 - not just refuses -
    when not allowed, so the route's existence isn't discoverable outside dev.

    Gated by settings.dev_fake_login_allowed, which requires BOTH an explicit
    opt-in (DEV_FAKE_LOGIN_ENABLED) and release_env naming a known non-prod
    environment - see src/settings.py. Turn DEV_FAKE_LOGIN_ENABLED off (or
    just don't set it) to go back to exercising the real passkey flow.
    """
    if not settings.dev_fake_login_allowed:
        raise HTTPException(status_code=404)

    logger.warning(
        f"DEV FAKE LOGIN used from {request.client.host} - bypassing WebAuthn entirely"
    )

    admin_user = await _get_admin_user()

    request.session["user_identifier"] = admin_user.pkid
    request.session["roles"] = admin_user.roles
    request.session["is_admin"] = True
    request.session["timezone"] = admin_user.my_timezone

    session_duration = timedelta(minutes=settings.max_age)
    request.session["exp"] = (datetime.now() + session_duration).timestamp()

    return RedirectResponse(url="/notes", status_code=303)


@router.get("/login/options")
async def login_options(request: Request):
    options = webauthn.generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    request.session[CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
    return Response(
        content=webauthn.options_to_json(options), media_type="application/json"
    )


@router.post("/login/verify")
async def login_verify(request: Request):
    challenge_b64 = request.session.pop(CHALLENGE_SESSION_KEY, None)
    if challenge_b64 is None:
        raise HTTPException(status_code=400, detail="No pending challenge")

    body = await request.body()

    try:
        parsed = parse_authentication_credential_json(body.decode("utf-8"))
    except Exception as exc:
        logger.warning(f"Malformed authentication response: {exc}")
        raise HTTPException(status_code=400, detail="Malformed passkey response")

    stored_cred = safe_record(
        await db_ops.read_one_record(
            Select(WebAuthnCredentials).where(
                WebAuthnCredentials.credential_id == bytes_to_base64url(parsed.raw_id)
            )
        )
    )
    if stored_cred is None:
        logger.warning("Passkey login attempted with unknown credential")
        raise HTTPException(status_code=401, detail="Unknown passkey")

    try:
        verification = webauthn.verify_authentication_response(
            credential=parsed,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=stored_cred.public_key,
            credential_current_sign_count=stored_cred.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        logger.warning(f"Passkey verification failed: {exc}")
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    user = safe_record(
        await db_ops.read_one_record(
            Select(Users).where(Users.pkid == stored_cred.user_id)
        )
    )
    if user is None or user.user_name != settings.admin_user.get_secret_value():
        raise HTTPException(status_code=401, detail="Unauthorized")

    await db_ops.execute_one(
        update(WebAuthnCredentials)
        .where(WebAuthnCredentials.pkid == stored_cred.pkid)
        .values(
            sign_count=verification.new_sign_count, date_last_used=datetime.utcnow()
        )
    )
    await db_ops.execute_one(
        update(Users)
        .where(Users.pkid == user.pkid)
        .values(date_last_login=datetime.utcnow())
    )

    request.session["user_identifier"] = user.pkid
    request.session["roles"] = user.roles
    request.session["is_admin"] = True
    request.session["timezone"] = user.my_timezone

    session_duration = timedelta(minutes=settings.max_age)
    request.session["exp"] = (datetime.now() + session_duration).timestamp()

    logger.info(f"Successful passkey login for {user.user_name}")
    return {"redirect": "/notes"}


@router.get("/register")
async def register_page(request: Request):
    if not await registration_open(request):
        return RedirectResponse(
            url="/?login_error=registration_closed", status_code=303
        )
    return templates.TemplateResponse(
        request=request, name="users/register.html", context={"request": request}
    )


@router.get("/register/options")
async def register_options(request: Request):
    if not await registration_authorized(request):
        raise HTTPException(status_code=403, detail="Registration is closed")

    admin_user = await _get_admin_user()

    existing_creds = safe_list(
        await db_ops.read_query(
            Select(WebAuthnCredentials).where(
                WebAuthnCredentials.user_id == admin_user.pkid
            )
        )
    )
    exclude_ids = [cred.credential_id for cred in existing_creds]

    options = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=admin_user.pkid.encode("utf-8"),
        user_name=admin_user.user_name,
        user_display_name=admin_user.user_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred_id))
            for cred_id in exclude_ids
        ],
    )
    request.session[CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
    return Response(
        content=webauthn.options_to_json(options), media_type="application/json"
    )


@router.post("/register/verify")
async def register_verify(request: Request):
    if not await registration_authorized(request):
        raise HTTPException(status_code=403, detail="Registration is closed")

    challenge_b64 = request.session.pop(CHALLENGE_SESSION_KEY, None)
    if challenge_b64 is None:
        raise HTTPException(status_code=400, detail="No pending challenge")

    device_name = request.query_params.get("device_name") or None
    body = await request.body()

    try:
        parsed = parse_registration_credential_json(body.decode("utf-8"))
    except Exception as exc:
        logger.warning(f"Malformed registration response: {exc}")
        raise HTTPException(status_code=400, detail="Malformed passkey response")

    try:
        verification = webauthn.verify_registration_response(
            credential=parsed,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        logger.warning(f"Passkey registration failed: {exc}")
        raise HTTPException(status_code=400, detail="Passkey registration failed")

    admin_user = await _get_admin_user()

    result = await db_ops.execute_one(
        insert(WebAuthnCredentials).values(
            pkid=str(uuid.uuid4()),
            user_id=admin_user.pkid,
            credential_id=bytes_to_base64url(verification.credential_id),
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=[t.value for t in (parsed.response.transports or [])],
            device_name=device_name,
        )
    )
    if is_db_error(result):
        raise HTTPException(status_code=500, detail="Failed to store passkey")

    logger.info(f"Registered new passkey for {admin_user.user_name}")
    return {"status": "ok"}


@router.get("/logout")
async def logout(request: Request):
    user_identifier = request.session.get("user_identifier", None)
    request.session.clear()
    logger.info(f"User {user_identifier} logged out")
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
