# -*- coding: utf-8 -*-
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, NoReturn

from dsg_lib.common_functions import logging_config
from dsg_lib.fastapi_functions import http_codes, system_health_endpoints
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.resources import templates
from src.settings import settings


def create_routes(app: FastAPI) -> NoReturn:
    logger.info("creating routes")
    app.mount("/statics", StaticFiles(directory="static"), name="statics")

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
    ) -> RedirectResponse:
        """
        Handles HTTP exceptions by redirecting to an error page.

        Args:
            request (Request): The request that caused the exception.
            exc (StarletteHTTPException): The exception that was raised.

        Returns:
            RedirectResponse: A response that redirects to an error page.
        """
        # Get the status code of the exception
        error_code = exc.status_code

        # If the status code is not in the dictionary of all HTTP codes, default to 500
        if error_code not in ALL_HTTP_CODES:
            error_code = 500  # default to Internal Server Error

        # Log the error
        logger.error(f"{error_code} error: {exc}")

        # Redirect to the error page for the status code
        return RedirectResponse(url=f"/error/{error_code}")

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
        return templates.TemplateResponse("error/error-page.html", context)

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
