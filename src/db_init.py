# -*- coding: utf-8 -*-
"""
Initializes the database connection for mrie. mrie owns its own independent
database/schema (see PROJECT_STATUS.md Phase 1) - this is no longer a
shared connection to devsetgo.com's (dsg) database. `db_driver` (see
Settings/DatabaseDriverEnum) picks between in-memory sqlite (`memory`, the
normal local-dev default), a real sqlite file under `sqlite_db/` (`sqlite`),
and Postgres.
"""
from dsg_lib.async_database_functions import async_database, database_config
from loguru import logger

from .settings import settings

if str(settings.db_driver.value).startswith(
    "sqlite+aiosqlite:///:memory:?cache=shared"
):  # no pragma: no cover
    db_uri: str = settings.db_driver.value
elif settings.db_driver.value == "sqlite+aiosqlite":  # no pragma: no cover
    db_name = settings.db_name.get_secret_value()
    if "." not in db_name:  # no pragma: no cover
        db_name = f"{db_name}.db"
    db_uri: str = f"{settings.db_driver.value}:///sqlite_db/{db_name}"
else:  # no pragma: no cover
    db_username = settings.db_username.get_secret_value()
    db_password = settings.db_password.get_secret_value()
    db_name = settings.db_name.get_secret_value()
    db_port = settings.db_port
    # postgresql://username:password@localhost:5432/mydatabase
    db_uri: str = (
        f"{settings.db_driver.value}://{db_username}:{db_password}@{settings.db_host}:{settings.db_port}/{db_name}"
    )
logger.debug(f"{db_uri}")


# Mapping of configuration options to database drivers that support them
option_support = {
    "echo": ["sqlite+aiosqlite", "postgresql", "mysql", "oracle", "mssql"],
    "future": ["sqlite+aiosqlite", "postgresql", "mysql", "oracle", "mssql"],
    "pool_pre_ping": ["postgresql", "mysql", "oracle", "mssql"],
    "pool_size": ["postgresql", "mysql", "oracle", "mssql"],
    "max_overflow": ["postgresql", "mysql", "oracle", "mssql"],
    "pool_recycle": ["sqlite+aiosqlite", "postgresql", "mysql", "oracle", "mssql"],
    "pool_timeout": ["postgresql", "mysql", "oracle", "mssql"],
}

config = {"database_uri": db_uri}

# Check each option
for option in [
    "echo",
    "future",
    "pool_pre_ping",
    "pool_size",
    "max_overflow",
    "pool_recycle",
    "pool_timeout",
]:
    value = getattr(settings, option)
    # Check if the driver string starts with a supported driver
    for driver in option_support[option]:
        if settings.db_driver.value.startswith(driver) and value is not None:
            config[option] = value
            break

logger.debug(f"database config: {config}")
logger.info("setting up database")
db_config = database_config.DBConfig(config)
async_db = async_database.AsyncDatabase(db_config)
