from contextlib import asynccontextmanager

from dsg_lib.common_functions import logging_config
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.resources import shutdown_event, startup_event
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
    yield
    await shutdown_event()
    logger.info("shutting down")


# Create an instance of the FastAPI class
app = FastAPI(
    title="DevSetGo.com",  # The title of the API
    description="Website for devsetgo.com",  # A brief description of the API
    version=settings.version,  # The version of the API
    docs_url="/docs",  # The URL where the API documentation will be served
    redoc_url="/redoc",  # The URL where the ReDoc documentation will be served
    openapi_url="/openapi.json",  # The URL where the OpenAPI schema will be served
    debug=settings.debug_mode,  # Enable debug mode
    middleware=[],  # A list of middleware to include in the application
    routes=[],  # A list of routes to include in the application
    lifespan=lifespan,
    # exception_handlers=
)


# Add GZip middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

from src.app_routes import create_routes
from src.resources import templates

create_routes(app)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    logger.info("index accessed")
    context = {"request": request}
    return templates.TemplateResponse(
        request=request, name="index.html", context=context
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
