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

# release_env values that count as "not production" - used to gate any
# dev-only behavior (the fake-login bypass, the raw-metrics debug card,
# anything added later). Deliberately an allowlist, not a denylist: an
# unset or mistyped RELEASE_ENV (e.g. "produciton") then fails closed
# instead of accidentally exposing something dev-only.
_DEV_ENVIRONMENTS = {"test", "dev", "local", "development"}


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
    phrase: SecretStr = Field(..., description="substitution cipher")
    salt: SecretStr = Field(..., description="salt for key derivation")
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
    # Dev-only login bypass so a reset in-memory dev DB doesn't force a fresh
    # WebAuthn registration on every restart. Opt-in (defaults off) and only
    # ever actually honored when is_dev_environment is true (see below) -
    # setting this alone does nothing in an environment that isn't
    # explicitly marked as non-production.
    dev_fake_login_enabled: bool = False

    @property
    def is_dev_environment(self) -> bool:
        return self.release_env.lower() in _DEV_ENVIRONMENTS

    @property
    def dev_fake_login_allowed(self) -> bool:
        return self.dev_fake_login_enabled and self.is_dev_environment

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
    # OpenAI Settings
    open_ai_disabled: bool = False
    openai_key: SecretStr = None  # OpenAI API Key
    openai_model: str = "gpt-5-nano"
    mood_analysis_weights: list = [
        ("elated", 1),
        ("overjoyed", 0.875),
        ("ecstatic", 0.75),
        ("joyful", 0.625),
        ("happy", 0.5),
        ("pleased", 0.375),
        ("content", 0.25),
        ("neutral", 0),
        ("concerned", -0.25),
        ("disappointed", -0.375),
        ("sad", -0.5),
        ("upset", -0.625),
        ("angry", -0.75),
        ("despair", -0.875),
        ("hopeless", -1),
    ]

    # WebAuthn (passkey login)
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "mikeryan.ie"
    webauthn_origin: str = "http://localhost:5000"
    # single-user access control: only this identity may log in/register a passkey
    admin_user: SecretStr = None
    # required to claim the very first passkey (see registration_authorized() in
    # src/endpoints/users.py) - without it, first-time registration is closed to everyone
    registration_bootstrap_token: SecretStr = None
    # historical data
    history_range: int = 1
    # self-hosted Plausible analytics
    plausible_domain: str = "mikeryan.ie"
    plausible_script_src: str = ""

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
