# -*- coding: utf-8 -*-
import sys
import time
from typing import NoReturn

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.settings import settings


def add_middleware(app: FastAPI) -> NoReturn:  # pragma: no cover
    """
    Adds middleware to the provided FastAPI application instance.

    This function adds GZipMiddleware for response compression, SessionMiddleware for managing user sessions,
    and a conditional AccessLoggerMiddleware for logging user access when the application is run with uvicorn.

    Args:
        app (FastAPI): The FastAPI application instance to which the middleware will be added.

    Returns:
        NoReturn
    """
    # Add GZipMiddleware for response compression
    # This middleware will compress responses for all requests that can handle it
    # The minimum_size argument specifies the minimum size a response must be before it can be compressed
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Add SessionMiddleware for managing user sessions
    # The secret_key argument is used to sign the session cookie
    # The same_site argument specifies that the session cookie should only be sent in requests from the same site
    # The https_only argument specifies that the session cookie should only be sent over HTTPS
    # The max_age argument specifies the maximum age of the session cookie in seconds
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        same_site=settings.same_site,  # can be Lax or None, but CSRF will be needed for None
        https_only=settings.https_only,
        max_age=settings.max_age,
    )

    # Check if the application is being run with uvicorn
    # If it is, add AccessLoggerMiddleware for logging user access
    # The user_identifier argument specifies the identifier to use for the user in the access logs
    if "uvicorn" in sys.argv[0]:
        app.add_middleware(
            AccessLoggerMiddleware, user_identifier=settings.session_user_identifier
        )
        logger.debug("AccessLoggerMiddleware added")
    else:
        logger.info("Surpressing AccessLoggerMiddleware as not running with uvicorn")


class AccessLoggerMiddleware(BaseHTTPMiddleware):  # pragma: no cover
    """
    Middleware to log all requests made to application
    """

    def __init__(self, app, user_identifier: str = "id"):
        super().__init__(app)
        self.user_identifier = user_identifier

    async def dispatch(self, request, call_next):
        # Record the start time
        start_time = time.time()
        try:
            # Call the next middleware or endpoint in the stack
            response = await call_next(request)
            # Get the status code from the response
            status_code = response.status_code
            logger.debug(f"Response: {response}")
        except Exception as e:
            # Log the exception and re-raise it
            logger.error(f"An error occurred while processing the request: {e}")
            raise

        # Calculate the processing time
        process_time = time.time() - start_time
        logger.debug(f"Processing time: {process_time}")
        # Get the request details
        method = request.method
        url = request.url
        client = request.client.host
        referer = request.headers.get("referer", "No referer")
        user_id = request.session.get(self.user_identifier, "unknown guest")
        headers = dict(request.headers.items())
        sensitive_headers = ["Authorization"]

        # Redact sensitive headers
        for header in sensitive_headers:
            if header in headers:
                headers[header] = "[REDACTED]"

        # Log the request details, but ignore favicon.ico requests
        if url.path != "/favicon.ico":
            logger.debug(
                {
                    "method": method,
                    "url": str(url),
                    "client": client,
                    "referer": referer,
                    "user_id": user_id,
                    "headers": headers,
                    "status_code": status_code,
                    "process_time": process_time,
                }
            )

        # Return the response
        return response
