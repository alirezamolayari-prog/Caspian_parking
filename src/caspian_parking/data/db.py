"""Engine factories for the gate-local SQLite database and the central SQL Server."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import Engine, create_engine, event

from caspian_parking.config.machine import ServerConfig

SQLITE_BUSY_TIMEOUT_MS = 30_000


def _sqlite_on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        # auto_vacuum only takes effect on a new, empty database (before the first table).
        cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")  # money tables: survive power cuts
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def create_sqlite_engine(path: Path, echo: bool = False) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        echo=echo,
        connect_args={"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000, "check_same_thread": False},
    )
    event.listen(engine, "connect", _sqlite_on_connect)
    return engine


def mssql_odbc_string(server: ServerConfig, password: str | None = None, database: str | None = None) -> str:
    parts = [
        f"DRIVER={{{server.driver}}}",
        f"SERVER={server.host}",
        f"DATABASE={database or server.database}",
        f"Encrypt={'yes' if server.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if server.trust_server_certificate else 'no'}",
        "APP=parking",
    ]
    if server.windows_auth:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={server.user}")
        parts.append(f"PWD={{{password or ''}}}")
    return ";".join(parts) + ";"


def create_mssql_engine(
    server: ServerConfig, password: str | None = None, database: str | None = None, echo: bool = False
) -> Engine:
    odbc = mssql_odbc_string(server, password, database)
    return create_engine(
        "mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc),
        echo=echo,
        fast_executemany=True,
        pool_pre_ping=True,
    )
