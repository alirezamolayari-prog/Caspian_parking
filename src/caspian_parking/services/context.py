"""Application context: data root, machine config, database and the logged-in user.

``open_context`` is the single start-up path used by the app, the smoke test and the tests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from caspian_parking.config.logging_setup import setup_logging
from caspian_parking.config.machine import MachineConfig, Role, load_machine_config, save_machine_config
from caspian_parking.config.paths import DataRoot, resolve_data_root
from caspian_parking.config.secrets import SecretStore
from caspian_parking.core.clock import SYSTEM_CLOCK, Clock
from caspian_parking.data.db import create_mssql_engine, create_sqlite_engine
from caspian_parking.data.migrate import upgrade
from caspian_parking.data.seed import register_node, seed_defaults
from caspian_parking.data.session import WriteContext, make_session_factory, read_session, unit_of_work
from caspian_parking.services.auth import CurrentUser

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    data_root: DataRoot
    config: MachineConfig
    engine: Engine
    session_factory: sessionmaker[Session]
    clock: Clock = SYSTEM_CLOCK
    training: bool = False
    user: CurrentUser | None = None
    locked_before: datetime | None = None
    secrets: SecretStore = field(init=False)
    server_engine: Engine | None = None  # gate: the central database (sync, reports when online)
    server_factory: sessionmaker[Session] | None = None
    link_online: bool = False

    def __post_init__(self) -> None:
        self.secrets = SecretStore(self.data_root.config)

    @property
    def node_id(self) -> str:
        return self.config.node_id

    @property
    def sync_mode(self) -> str | None:
        """Gates queue their writes for the server; the server logs them for the gates (SPEC §2.2)."""
        if self.training:
            return None
        if self.config.role is Role.GATE:
            return "outbox"
        if self.config.role is Role.SERVER:
            return "log"
        return None

    def write_context(self, reason: str | None = None) -> WriteContext:
        return WriteContext(
            node_id=self.node_id,
            user_id=self.user.id if self.user else None,
            clock=self.clock,
            reason=reason,
            locked_before=self.locked_before,
            sync=self.sync_mode,
        )

    @contextmanager
    def uow(self, reason: str | None = None) -> Iterator[Session]:
        with unit_of_work(self.session_factory, self.write_context(reason)) as session:
            yield session

    @contextmanager
    def read(self) -> Iterator[Session]:
        with read_session(self.session_factory) as session:
            yield session

    def can(self, permission: str) -> bool:
        return self.user is not None and self.user.can(permission)

    def save_config(self) -> None:
        save_machine_config(self.data_root.config, self.config)

    def close(self) -> None:
        self.engine.dispose()
        if self.server_engine is not None:
            self.server_engine.dispose()


SQL_PASSWORD_SECRET = "sql_password"


def server_engine_for(config: MachineConfig, secrets: SecretStore) -> Engine:
    """Engine for the central SQL Server (connects lazily; ODBC driver name is a setting)."""
    return create_mssql_engine(config.server, password=secrets.get_text(SQL_PASSWORD_SECRET))


def open_context(
    root: Path | None = None,
    clock: Clock = SYSTEM_CLOCK,
    training: bool | None = None,
    log_console: bool = False,
    engine: Engine | None = None,
    server_engine: Engine | None = None,
) -> AppContext:
    """Open this PC's database.

    * standalone / gate: local SQLite (a gate also gets the central database for sync and reports),
    * server: the central SQL Server database itself (``engine`` can be injected, e.g. in tests).
    """
    data_root = DataRoot(root or resolve_data_root()).ensure()
    setup_logging(data_root.logs, console=log_console)
    config = load_machine_config(data_root.config)
    use_training = config.training_mode if training is None else training
    db_path = data_root.training_db if use_training else data_root.local_db
    secret_store = SecretStore(data_root.config)
    if engine is None:
        if config.role is Role.SERVER and not use_training and config.server.host:
            engine = server_engine_for(config, secret_store)
        else:
            engine = create_sqlite_engine(db_path)
    upgrade(engine)
    factory = make_session_factory(engine)
    context = AppContext(data_root, config, engine, factory, clock=clock, training=use_training)
    if config.role is Role.GATE and not use_training:
        if server_engine is None and config.server.host:
            server_engine = server_engine_for(config, secret_store)
        if server_engine is not None:
            context.server_engine = server_engine
            context.server_factory = make_session_factory(server_engine)
    with context.uow() as session:
        seed_defaults(session)
        register_node(session, config)
    from caspian_parking.services.fiscal import ensure_fiscal_year, locked_before

    with context.uow() as session:
        ensure_fiscal_year(session, clock.now_utc())
        context.locked_before = locked_before(session)
    if config.role is Role.SERVER and not use_training:
        from caspian_parking.services.gate_service import HMAC_SECRET
        from caspian_parking.services.sync import ensure_server_log, publish_ticket_key

        ensure_server_log(engine, config.node_id)
        context.secrets.set(HMAC_SECRET, publish_ticket_key(engine, context.secrets.get_or_create(HMAC_SECRET)))
    log.info("opened %s database (%s)", "training" if use_training else config.role.value, engine.dialect.name)
    return context
