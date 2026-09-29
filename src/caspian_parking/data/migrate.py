"""Programmatic Alembic: upgrade any engine (SQLite or SQL Server) to the latest schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), revision)


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def revisions_in_order() -> list[str]:
    """All revision ids from the first to head."""
    script = ScriptDirectory.from_config(alembic_config())
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]
