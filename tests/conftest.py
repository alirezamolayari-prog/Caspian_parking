"""Shared pytest configuration. Qt always runs offscreen in tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from caspian_parking.core.clock import FixedClock
from caspian_parking.core.ids import uuid7
from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.data.migrate import upgrade
from caspian_parking.data.session import WriteContext, make_session_factory
from caspian_parking.services.context import AppContext, open_context

START = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)  # 11:30 Tehran, Monday 1405/07/06


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_sqlite_engine(tmp_path / "test.db")
    upgrade(engine)
    yield engine
    engine.dispose()


@cache
def _mssql_reason() -> str | None:
    from tests.support.mssql import unavailable_reason

    return unavailable_reason()


@pytest.fixture(scope="session")
def mssql_engine_session():
    reason = _mssql_reason()
    if reason:
        pytest.skip(reason)
    from tests.support.mssql import temporary_database

    with temporary_database() as engine:
        upgrade(engine)
        yield engine


@pytest.fixture(params=["sqlite", pytest.param("mssql", marks=pytest.mark.mssql)])
def any_engine(request, tmp_path):
    """Run a test on both dialects."""
    if request.param == "sqlite":
        engine = create_sqlite_engine(tmp_path / "any.db")
        upgrade(engine)
        yield engine
        engine.dispose()
    else:
        yield request.getfixturevalue("mssql_engine_session")


@pytest.fixture
def write_ctx(clock) -> WriteContext:
    return WriteContext(node_id=uuid7(), user_id=None, clock=clock)


@pytest.fixture
def factory(sqlite_engine):
    return make_session_factory(sqlite_engine)


@pytest.fixture
def app_ctx(tmp_path, clock) -> Iterator[AppContext]:
    context = open_context(tmp_path / "data", clock=clock)
    yield context
    context.close()
