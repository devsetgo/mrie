# -*- coding: utf-8 -*-
import secrets  # For generating secure random numbers
from datetime import datetime  # A Python library used for working with dates and times
from enum import (
    Enum,  # For creating enumerations, which are a set of symbolic names bound to unique constant values
)
from functools import lru_cache  # For caching the results of expensive function calls
from typing import Optional

from loguru import logger  # For logging
from pydantic import (  # For validating data
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__


class SameSiteEnum(str, Enum):
    Lax = "Lax"
    Strict = "Strict"
    None_ = "None"


class DatabaseDriverEnum(str, Enum):
    postgres = "postgresql+asyncpg"
    postgresql = "postgresql+asyncpg"
    sqlite = "sqlite+aiosqlite"
    memory = "sqlite+aiosqlite:///:memory:?cache=shared"
    # mysql = "mysql+aiomysql"
    # oracle = "oracle+cx_oracle"

    model_config = ConfigDict(use_enum_values=True, extra="allow")


class Settings(BaseSettings):
    # Class that describes the settings schema
    https_redirect: bool = False
    # allowed_hosts: list = ["localhost", "devsetgo.com","*.devsetgo.com","0.0.0.0"]
    # database_configuration: DatabaseSettings = DatabaseSettings()
    db_driver: DatabaseDriverEnum = Field("memory", description="DB_DRIVER")
    db_username: SecretStr = Field(..., description="DB_USERNAME")
    db_password: SecretStr = Field(..., description="DB_PASSWORD")
    db_host: str = Field(..., description="DB_HOST")
    db_port: int = Field(..., description="DB_PORT")
    db_name: SecretStr = Field(
        ..., description="For sqlite it should be folder path 'folder/filename"
    )
    echo: bool = Field(True, description="Enable echo")
    future: bool = Field(True, description="Enable future")
    pool_pre_ping: bool = Field(False, description="Enable pool_pre_ping")
    pool_size: Optional[int] = Field(None, description="Set pool_size")
    max_overflow: Optional[int] = Field(None, description="Set max_overflow")
    pool_recycle: int = Field(3600, description="Set pool_recycle")
    pool_timeout: Optional[int] = Field(None, description="Set pool_timeout")

    # Set the current date and time when the application is run
    date_run: datetime = datetime.utcnow()
    # application settings
    release_env: str = "prd"
    version: str = __version__
    debug_mode: bool = False
    # logging settings
    logging_directory: str = "log"
    log_name: str = "log.log"
    logging_level: str = "INFO"
    log_rotation: str = "100 MB"
    log_retention: str = "30 days"
    log_backtrace: bool = False
    log_serializer: bool = False
    log_diagnose: bool = False
    log_intercept_standard_logging: bool = False
    # session management
    max_failed_login_attempts: int = 5
    session_secret_key: str = secrets.token_hex(32)  # Generate a random secret key
    same_site: SameSiteEnum = Field("Lax", description="Options: Lax, Strict, None")
    https_only: bool = False
    max_age: int = 3600
    session_user_identifier: str = "user_identifier"
    # service accounts
    default_timezone: str = "America/New_York"

    @model_validator(mode="before")
    @classmethod
    def parse_database_driver(cls, values):
        db_driver = values.get("db_driver")
        if isinstance(db_driver, str):
            try:
                # Convert db_driver to lower case before getting its value from the enum
                values["db_driver"] = DatabaseDriverEnum[db_driver.lower()].value
            except KeyError:
                pass
        return values

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
        # use_enum_values=True
    )  # Set up the configuration dictionary for the settings


@lru_cache
def get_settings():
    # Function to get an instance of the Settings class. The results are cached
    # to improve performance.
    logger.debug(f"Settings: {Settings().model_dump()}")
    return Settings()


settings = get_settings()  # Get the settings
