"""Monthly database maintenance (SPEC §2.4): SQLite ANALYZE + optimize + incremental vacuum;
SQL Server statistics update and index rebuild."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import Engine, inspect, text

from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting, set_setting

log = logging.getLogger(__name__)

EVERY = timedelta(days=30)


def is_due(ctx: AppContext) -> bool:
    with ctx.read() as session:
        last = str(get_setting(session, "maintenance.last_run") or "")
    return not last or ctx.clock.now_utc() - datetime.fromisoformat(last) >= EVERY


def run_sqlite_maintenance(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("ANALYZE")
        connection.exec_driver_sql("PRAGMA optimize")
        connection.exec_driver_sql("PRAGMA incremental_vacuum")
        connection.commit()


def run_mssql_maintenance(engine: Engine) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in inspect(engine).get_table_names():
            connection.execute(text(f"ALTER INDEX ALL ON [{table}] REBUILD"))
        connection.execute(text("EXEC sp_updatestats"))


def run_maintenance(ctx: AppContext, force: bool = False) -> bool:
    if not force and not is_due(ctx):
        return False
    if ctx.engine.dialect.name == "mssql":
        run_mssql_maintenance(ctx.engine)
    else:
        run_sqlite_maintenance(ctx.engine)
    with ctx.uow(reason="maintenance") as session:
        set_setting(session, "maintenance.last_run", ctx.clock.now_utc().isoformat())
    log.info("database maintenance done")
    return True
