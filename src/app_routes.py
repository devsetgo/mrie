# -*- coding: utf-8 -*-
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, NoReturn

from dsg_lib.common_functions import logging_config
from dsg_lib.fastapi_functions import http_codes, system_health_endpoints
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.resources import templates
from src.settings import settings
from src.endpoints import about, notes, users, web_links


def create_routes(app: FastAPI) -> NoReturn:
    logger.info("creating routes")
    app.mount("/statics", StaticFiles(directory="static"), name="statics")

    app.include_router(
        users.router, prefix="/users", tags=["users"], include_in_schema=False
    )
    app.include_router(
        notes.router, prefix="/notes", tags=["notes"], include_in_schema=False
    )
    app.include_router(
        web_links.router, prefix="/weblinks", tags=["weblinks"], include_in_schema=False
    )
    app.include_router(
        about.router, prefix="/about", tags=["about"], include_in_schema=False
    )

    t0 = time.time()
    site_error_routing_codes: list = [
        400,
        401,
        402,
        403,
        404,
        405,
        406,
        407,
        408,
        409,
        410,
        411,
        412,
        413,
        414,
        415,
        416,
        417,
        418,
        421,
        422,
        423,
        424,
        425,
        426,
        428,
        429,
        431,
        451,
        500,
        501,
        502,
        503,
        504,
        505,
        506,
        507,
        508,
        510,
        511,
    ]
    # Generate a dictionary of all HTTP codes
    ALL_HTTP_CODES: Dict[int, Dict[str, Any]] = http_codes.generate_code_dict(
        site_error_routing_codes
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        """
        Handles HTTP exceptions by redirecting to an error page for browser page
        navigations, or returning a plain JSON body for API/fetch calls (e.g. the
        WebAuthn endpoints in src/endpoints/users.py) - those callers check
        `response.ok`/`response.status`, and `fetch()` follows redirects
        transparently, which would otherwise turn a failed API call into a
        false-positive 200 with an HTML body.

        A 401 during a page navigation (check_login rejecting a missing,
        expired, or dangling session - e.g. the session's user_identifier no
        longer exists after the in-memory dev DB reset on restart) is a
        special case: landing on a bare "401 Unauthorized" page with no way
        forward is a dead end. Clear the broken session and send the browser
        to the login page instead, so the fix is just "log back in."

        Args:
            request (Request): The request that caused the exception.
            exc (StarletteHTTPException): The exception that was raised.

        Returns:
            Response: A JSON error body for API calls, a redirect to the login
                page for a 401 page navigation, or a redirect to the HTML
                error page for everything else.
        """
        # Log the error
        logger.error(f"{exc.status_code} error: {exc}")

        accept_header = request.headers.get("accept", "")
        wants_json = "application/json" in accept_header
        logger.debug(
            f"http_exception_handler: path={request.url.path!r} "
            f"accept={accept_header!r} wants_json={wants_json}"
        )

        if wants_json:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )

        if exc.status_code == 401:
            request.session.clear()
            return RedirectResponse(url="/users/login", status_code=303)

        # Get the status code of the exception
        error_code = exc.status_code

        # If the status code is not in the dictionary of all HTTP codes, default to 500
        if error_code not in ALL_HTTP_CODES:
            error_code = 500  # default to Internal Server Error

        # Redirect to the error page for the status code. status_code=303 forces
        # the browser to GET the error page regardless of the original request's
        # method - the default 307 would instead resubmit e.g. a failed POST,
        # which /error/{code} (GET-only) would reject with 405, which would
        # redirect back here again via 307, looping forever
        # (net::ERR_TOO_MANY_REDIRECTS).
        return RedirectResponse(url=f"/error/{error_code}", status_code=303)

    show_route: bool = False

    @app.get("/error/{error_code}", include_in_schema=False)
    async def error_page(request: Request, error_code: int) -> Dict[str, Any]:
        """
        Returns an error page for the specified error code.

        Args:
            request (Request): The request that caused the error.
            error_code (int): The error code.

        Returns:
            Dict[str, Any]: A dictionary that represents the context of the error page.
        """
        # Create the context for the error page
        context = {
            "request": request,
            "error_code": error_code,
            "description": ALL_HTTP_CODES[error_code]["description"],
            "extended_description": ALL_HTTP_CODES[error_code]["extended_description"],
            "link": ALL_HTTP_CODES[error_code]["link"],
        }

        # Return a template response with the error page and the context
        return templates.TemplateResponse(
            request=request, name="error/error-page.html", context=context
        )

    # This should always be the last route added to keep it at the bottom of the OpenAPI docs
    config_health = {
        "enable_status_endpoint": True,
        "enable_uptime_endpoint": True,
        "enable_heapdump_endpoint": True,
    }

    app.include_router(
        system_health_endpoints.create_health_router(config=config_health),
        prefix="/api/health",
        tags=["system-health"],
        include_in_schema=True,
    )
    # Log the time it took to create the routes
    logger.info(f"Routes created in {time.time()-t0:.4f} seconds")
    return None
