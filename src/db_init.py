# -*- coding: utf-8 -*-
"""
Initializes the database connection for mrie. mrie runs on SQLite: a single
file under `settings.db_directory` (a Docker volume in production - see
docker-compose.yml), or a throwaway in-memory database (`memory`, what the
test suite always uses).
"""

import os

from dsg_lib.async_database_functions import async_database, database_config
from loguru import logger
from sqlalchemy import event

from .settings import DatabaseDriverEnum, settings

SQLITE_AIOSQLITE_DRIVER = "sqlite+aiosqlite"

if settings.db_driver == DatabaseDriverEnum.memory:  # no pragma: no cover
    db_uri: str = settings.db_driver.value
    db_path: str | None = None
else:  # no pragma: no cover
    db_name = settings.db_name
    if "." not in db_name:  # no pragma: no cover
        db_name = f"{db_name}.db"
    db_path = os.path.join(settings.db_directory, db_name)
    # SQLite creates the file but not its folder - a missing folder is a
    # confusing "unable to open database file" at first query otherwise.
    os.makedirs(settings.db_directory, exist_ok=True)
    db_uri = f"{SQLITE_AIOSQLITE_DRIVER}:///{db_path}"
logger.debug(f"{db_uri}")

config = {"database_uri": db_uri}
for option in ["echo", "future", "pool_recycle"]:
    value = getattr(settings, option)
    if value is not None:
        config[option] = value

logger.debug(f"database config: {config}")
logger.info("setting up database")
db_config = database_config.DBConfig(config)
async_db = async_database.AsyncDatabase(db_config)


@event.listens_for(db_config.engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """
    Per-connection SQLite tuning. PRAGMAs (other than journal_mode) don't
    persist in the file, so they have to be re-applied on every connection
    the pool opens.

    - journal_mode=WAL: readers never block the writer and vice versa, and a
      commit appends to the WAL instead of rewriting pages in place - the
      single biggest win for a web app on SQLite. Persistent in the file
      once set; skipped for :memory:, which can't use WAL.
    - synchronous=NORMAL: safe under WAL (a power loss can drop the last
      commits, never corrupt the file) and avoids an fsync on every commit.
    - busy_timeout: wait for another worker process's write lock instead of
      failing immediately with "database is locked".
    - foreign_keys: SQLite ignores FOREIGN KEY constraints unless asked.
    - temp_store/cache_size/mmap_size: keep sorts, temp indexes and hot
      pages in memory.
    """
    cursor = dbapi_connection.cursor()
    try:
        if db_path is not None:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA mmap_size={int(settings.sqlite_mmap_size_bytes)}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={int(settings.sqlite_busy_timeout_ms)}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute(f"PRAGMA cache_size=-{int(settings.sqlite_cache_size_kib)}")
    finally:
        cursor.close()
