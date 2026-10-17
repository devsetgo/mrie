# -*- coding: utf-8 -*-
import os
import secrets  # For generating secure random numbers
from datetime import (  # A Python library used for working with dates and times
    datetime,
    timezone,
)
from enum import (
    Enum,  # For creating enumerations, which are a set of symbolic names bound to unique constant values
)
from functools import lru_cache  # For caching the results of expensive function calls
from typing import Optional

from loguru import logger  # For logging
from pydantic import (  # For validating data
    ConfigDict,
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

# The marker deploy/.env.pre-prd uses for values that must be filled in.
# Outside dev a setting still holding it is refused at startup: left in
# SESSION_SECRET_KEY or REGISTRATION_BOOTSTRAP_TOKEN it would be a secret
# anyone who has read the template knows.
_UNFILLED_PLACEHOLDER = "__FILL_ME__"
_MIN_SESSION_SECRET_KEY_LENGTH = 32


# Docker secrets are mounted as one file per secret, named after the
# secret's target (e.g. /run/secrets/openai_key). pydantic-settings reads
# any file here whose name matches a field name, case-insensitively and with
# the trailing newline stripped, so docker-compose.yml only has to mount
# `phrase`, `salt`, `openai_key`, ... - no *_FILE plumbing per field.
# Precedence is real env vars, then .env, then these files, so a local .env
# still wins in development. Only enabled when the directory exists:
# pydantic-settings warns on every start about a missing one.
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
SECRETS_DIR = _SECRETS_DIR if os.path.isdir(_SECRETS_DIR) else None


class SameSiteEnum(str, Enum):
    Lax = "Lax"
    Strict = "Strict"
    None_ = "None"


class DatabaseDriverEnum(str, Enum):
    sqlite = "sqlite+aiosqlite"
    memory = "sqlite+aiosqlite:///:memory:?cache=shared"

    model_config = ConfigDict(use_enum_values=True, extra="allow")


class Settings(BaseSettings):
    # Class that describes the settings schema
    # The enum member, not "sqlite": pydantic-settings validates defaults,
    # and parse_database_driver only maps the short names it is given.
    db_driver: DatabaseDriverEnum = Field(
        DatabaseDriverEnum.sqlite, description="DB_DRIVER"
    )
    db_directory: str = Field(
        "sqlite_db", description="Folder holding the SQLite file (a Docker volume)"
    )
    db_name: str = Field("mrie", description="SQLite file name; '.db' is appended")
    phrase: SecretStr = Field(..., description="substitution cipher")
    salt: SecretStr = Field(..., description="salt for key derivation")
    # Off unless asked for: echo logs every statement *with its bound
    # parameters*, i.e. note ciphertext, link URLs and session data.
    echo: bool = Field(False, description="Enable echo")
    future: bool = Field(True, description="Enable future")
    pool_recycle: int = Field(3600, description="Set pool_recycle")
    # SQLite tuning, applied to every new connection (see db_init.py).
    # busy_timeout: how long a writer waits on another process's write lock
    # before raising "database is locked" - with WEB_CONCURRENCY > 1 the
    # workers share one file, and writes are serialized across them.
    sqlite_busy_timeout_ms: int = 5000
    # Negative = KiB, per connection. 64 MiB keeps the whole working set of a
    # single-user journal in memory without re-reading pages from disk.
    sqlite_cache_size_kib: int = 65536
    # Memory-mapped reads skip a syscall + copy per page. 256 MiB is an upper
    # bound, not an allocation - only the file's actual size gets mapped.
    sqlite_mmap_size_bytes: int = 268435456

    # Set the current date and time when the application is run
    date_run: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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

    @property
    def is_memory_db(self) -> bool:
        """True for the throwaway in-memory database the test suite uses.

        Its schema comes from create_tables() on boot; a file database's
        comes from Alembic (see resources.py::startup_event()).
        """
        return self.db_driver == DatabaseDriverEnum.memory

    # logging settings
    logging_directory: str = "logs"
    log_name: str = "log"
    logging_level: str = "INFO"
    log_rotation: str = "100 MB"
    log_retention: str = "30 days"
    log_backtrace: bool = False
    log_serializer: bool = False
    log_diagnose: bool = False
    log_intercept_standard_logging: bool = False
    # session management
    # Required outside dev (see require_production_secrets below): uvicorn
    # runs WEB_CONCURRENCY workers, and a per-process random key would make
    # each worker reject the others' cookies - random logouts, and a passkey
    # ceremony whose begin/complete hit different workers always failing.
    session_secret_key: Optional[SecretStr] = Field(
        None, description="Signs the session cookie"
    )
    same_site: SameSiteEnum = Field("Lax", description="Options: Lax, Strict, None")
    https_only: bool = False
    max_age: int = 3600
    session_user_identifier: str = "user_identifier"
    # service accounts
    default_timezone: str = "America/New_York"
    # OpenAI Settings
    openai_key: Optional[SecretStr] = None  # OpenAI API Key
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
    admin_user: Optional[SecretStr] = None
    # required to claim the very first passkey (see registration_authorized() in
    # src/endpoints/users.py) - without it, first-time registration is closed to everyone
    registration_bootstrap_token: Optional[SecretStr] = None
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

    @model_validator(mode="after")
    def require_production_secrets(self):
        if self.is_dev_environment:
            if self.session_secret_key is None:
                self.session_secret_key = SecretStr(secrets.token_hex(32))
            return self

        unfilled = [
            name.upper()
            for name, value in self.model_dump().items()
            if _UNFILLED_PLACEHOLDER
            in (
                value.get_secret_value() if isinstance(value, SecretStr) else str(value)
            )
        ]
        if unfilled:
            raise ValueError(
                f"Fill in {', '.join(unfilled)} (still {_UNFILLED_PLACEHOLDER})"
            )
        key = self.session_secret_key
        if key is None or len(key.get_secret_value()) < _MIN_SESSION_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SESSION_SECRET_KEY must be at least {_MIN_SESSION_SECRET_KEY_LENGTH} "
                "characters when RELEASE_ENV is not a dev value - every worker "
                "must sign cookies with the same, unguessable key"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=SECRETS_DIR,
        extra="allow",
        # A failed validation otherwise prints the whole input dict - PHRASE,
        # SALT, the OpenAI key - into the container log at startup.
        hide_input_in_errors=True,
    )  # Set up the configuration dictionary for the settings


@lru_cache
def get_settings():
    # Function to get an instance of the Settings class. The results are cached
    # to improve performance.
    settings_instance = Settings()
    # extra="allow" means any undeclared env var (e.g. a *_secret/*_token name
    # nobody thought to add to the schema) becomes a plain, unmasked str field
    # here - unlike the SecretStr fields declared above, model_dump() would
    # render those in cleartext. Excluding model_extra keeps this log useful
    # for the known schema while never printing an arbitrary future secret.
    safe_settings = settings_instance.model_dump(
        exclude=set(settings_instance.model_extra or {})
    )
    logger.debug(f"Settings: {safe_settings}")
    return settings_instance


settings = get_settings()  # Get the settings
