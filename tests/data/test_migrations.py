"""Schema/migration checks on both dialects (SPEC §10: upgrade without data loss)."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from caspian_parking.data.base import Base
from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.data.migrate import (
    current_revision,
    downgrade,
    head_revision,
    revisions_in_order,
    upgrade,
)
from caspian_parking.data.session import append_only_tables


def _diff(engine):
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)


def test_models_match_migrations(any_engine):
    assert current_revision(any_engine) == head_revision()
    assert _diff(any_engine) == []


def test_sqlite_pragmas(sqlite_engine):
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA auto_vacuum")).scalar() == 2  # INCREMENTAL


def test_every_event_table_has_triggers(any_engine):
    tables = append_only_tables()
    assert {"audit_log", "shift_events"} <= tables
    with any_engine.connect() as conn:
        if any_engine.dialect.name == "sqlite":
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).scalars().all()
            for table in tables:
                assert f"trg_{table}_no_update" in rows
                assert f"trg_{table}_no_delete" in rows
        else:
            rows = conn.execute(text("SELECT name FROM sys.triggers")).scalars().all()
            for table in tables:
                assert f"trg_{table}_append_only" in rows


def test_stepwise_upgrade_keeps_data(tmp_path):
    """Walk every revision; rows written at each step must survive all later upgrades."""
    engine = create_sqlite_engine(tmp_path / "steps.db")
    revisions = revisions_in_order()
    assert revisions, "no migrations found"
    seeded: list[tuple[str, int]] = []
    for index, revision in enumerate(revisions):
        upgrade(engine, revision)
        with engine.begin() as conn:
            code = 100 + index
            conn.execute(
                text(
                    "INSERT INTO gates (id, origin_node, created_at_utc, row_version, updated_at_utc, is_active,"
                    " code, name, sort) VALUES (:id, 'n', '2026-01-01 00:00:00', 1, '2026-01-01 00:00:00', 1,"
                    " :code, :name, 0)"
                ),
                {"id": f"id-{index}", "code": code, "name": f"gate {revision}"},
            )
            seeded.append((f"id-{index}", code))
    with engine.connect() as conn:
        for row_id, code in seeded:
            assert conn.execute(text("SELECT code FROM gates WHERE id = :id"), {"id": row_id}).scalar() == code
    engine.dispose()


def test_downgrade_to_base_and_back(tmp_path):
    engine = create_sqlite_engine(tmp_path / "down.db")
    upgrade(engine)
    downgrade(engine, "base")
    assert "gates" not in inspect(engine).get_table_names()
    upgrade(engine)
    assert current_revision(engine) == head_revision()
    engine.dispose()


@pytest.mark.parametrize("table", ["gates", "users", "settings", "audit_log"])
def test_core_tables_exist(sqlite_engine, table):
    assert table in inspect(sqlite_engine).get_table_names()
