from contextlib import asynccontextmanager

from dsg_lib.common_functions import logging_config
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.settings import settings

templates = Jinja2Templates(directory="templates")


async def startup_event():
    logger.info("starting up")
    return None


async def shutdown_event():
    logger.info("shutting down")
    return None
