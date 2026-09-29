"""Database triggers that make event tables append-only (second line of defence after the ORM)."""

from __future__ import annotations


def create_trigger_sql(dialect: str, table: str) -> list[str]:
    if dialect == "sqlite":
        return [
            f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'append-only table {table}'); END",
            f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'append-only table {table}'); END",
        ]
    if dialect == "mssql":
        return [
            f"CREATE TRIGGER trg_{table}_append_only ON {table} INSTEAD OF UPDATE, DELETE AS "
            f"BEGIN SET NOCOUNT ON; THROW 51000, 'append-only table {table}', 1; END"
        ]
    raise ValueError(f"unsupported dialect {dialect}")


def drop_trigger_sql(dialect: str, table: str) -> list[str]:
    if dialect == "sqlite":
        return [f"DROP TRIGGER IF EXISTS trg_{table}_no_update", f"DROP TRIGGER IF EXISTS trg_{table}_no_delete"]
    if dialect == "mssql":
        return [f"IF OBJECT_ID('trg_{table}_append_only', 'TR') IS NOT NULL DROP TRIGGER trg_{table}_append_only"]
    raise ValueError(f"unsupported dialect {dialect}")
