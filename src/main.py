# -*- coding: utf-8 -*-
from contextlib import asynccontextmanager

from dsg_lib.common_functions import logging_config
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from loguru import logger

from src.app_middleware import add_middleware
from src.app_routes import create_routes
from src.resources import shutdown_event, startup_event, templates
from src.settings import settings

logging_config.config_log(
    logging_directory=settings.logging_directory,
    log_name=settings.log_name,
    logging_level=settings.logging_level,
    log_rotation=settings.log_rotation,
    log_retention=settings.log_retention,
    log_backtrace=settings.log_backtrace,
    log_format=None,
    log_serializer=settings.log_serializer,
    log_diagnose=settings.log_diagnose,
    intercept_standard_logging=settings.log_intercept_standard_logging,
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    logger.info("starting up")
    await startup_event()
    try:
        yield
    finally:
        # Runs even when the app exits with an error, so the DB engine is
        # always disposed (WAL checkpoint, no dangling connections).
        await shutdown_event()
        logger.info("shutting down")


# Create an instance of the FastAPI class
app = FastAPI(
    title="DevSetGo.com",  # The title of the API
    description="Website for devsetgo.com",  # A brief description of the API
    version=settings.version,  # The version of the API
    # API docs only in dev: in production they would hand anyone a map of
    # every endpoint and parameter, and nothing in the app consumes them.
    docs_url="/docs" if settings.is_dev_environment else None,
    redoc_url="/redoc" if settings.is_dev_environment else None,
    openapi_url="/openapi.json" if settings.is_dev_environment else None,
    debug=settings.debug_mode,  # Enable debug mode
    middleware=[],  # A list of middleware to include in the application
    routes=[],  # A list of routes to include in the application
    lifespan=lifespan,
    # exception_handlers=
)


# Add GZip middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# add middleware and routes to the application
add_middleware(app)
# create routes
create_routes(app)


@app.get("/")
async def read_root(request: Request):
    # mrie is single-user: once logged in, "/" has nothing for you that
    # /notes doesn't - send you straight to the app instead of the public
    # logged-out landing page.
    if request.session.get("user_identifier") is not None:
        return RedirectResponse(url="/notes")

    logger.info("index accessed")
    context = {"request": request}
    return templates.TemplateResponse(
        request=request, name="index.html", context=context
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
