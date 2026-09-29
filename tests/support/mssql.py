"""Test helper: a throw-away database on SQL Server LocalDB (see DECISIONS D-017)."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc
from sqlalchemy import Engine

from caspian_parking.config.machine import ServerConfig
from caspian_parking.data.db import create_mssql_engine

PREFERRED_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
)
LOCALDB_INSTANCE = os.environ.get("PARKING_TEST_LOCALDB", "v11.0")


def available_driver() -> str | None:
    installed = set(pyodbc.drivers())
    return next((d for d in PREFERRED_DRIVERS if d in installed), None)


def localdb_pipe() -> str | None:
    """Start the LocalDB instance and return its named-pipe address, or None."""
    try:
        subprocess.run(["sqllocaldb", "start", LOCALDB_INSTANCE], capture_output=True, timeout=120, check=False)
        info = subprocess.run(
            ["sqllocaldb", "info", LOCALDB_INSTANCE], capture_output=True, text=True, timeout=60, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(np:\S+)", info or "")
    return match.group(1) if match else None


def unavailable_reason() -> str | None:
    if available_driver() is None:
        return "no SQL Server ODBC driver installed"
    if localdb_pipe() is None:
        return f"SQL Server LocalDB instance {LOCALDB_INSTANCE} not available"
    return None


def _server_config(pipe: str, driver: str) -> ServerConfig:
    return ServerConfig(host=pipe, database="master", driver=driver, windows_auth=True)


@contextmanager
def temporary_database() -> Iterator[Engine]:
    driver = available_driver()
    pipe = localdb_pipe()
    if driver is None or pipe is None:
        raise RuntimeError("SQL Server LocalDB not available")
    name = "parking_test_" + uuid.uuid4().hex[:12]
    server = _server_config(pipe, driver)
    master = pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={pipe};Trusted_Connection=yes;DATABASE=master;", autocommit=True, timeout=60
    )
    try:
        master.execute(f"CREATE DATABASE [{name}]")
        master.execute(f"ALTER DATABASE [{name}] SET READ_COMMITTED_SNAPSHOT ON")
        engine = create_mssql_engine(server, database=name)
        try:
            yield engine
        finally:
            engine.dispose()
            master.execute(f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            master.execute(f"DROP DATABASE [{name}]")
    finally:
        master.close()
