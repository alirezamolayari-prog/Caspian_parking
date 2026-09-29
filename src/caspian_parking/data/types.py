"""Portable column types for SQLite and SQL Server."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, UnicodeText
from sqlalchemy.dialects import mssql
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from caspian_parking.core.clock import ensure_utc

ID_LENGTH = 36


def IdType() -> String:
    return String(ID_LENGTH)


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetime in Python, naive UTC in the database (DATETIME2 on SQL Server)."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "mssql":
            return dialect.type_descriptor(mssql.DATETIME2(precision=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class JSONText(TypeDecorator[Any]):
    """JSON stored as Unicode text (SQL Server 2012 has no JSON type)."""

    impl = UnicodeText
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        return json.loads(value)
