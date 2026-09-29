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
from caspian_parking.config.machine import MachineConfig, load_machine_config, save_machine_config
from caspian_parking.config.paths import DataRoot, resolve_data_root
from caspian_parking.config.secrets import SecretStore
from caspian_parking.core.clock import SYSTEM_CLOCK, Clock
from caspian_parking.data.db import create_sqlite_engine
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

    def __post_init__(self) -> None:
        self.secrets = SecretStore(self.data_root.config)

    @property
    def node_id(self) -> str:
        return self.config.node_id

    def write_context(self, reason: str | None = None) -> WriteContext:
        return WriteContext(
            node_id=self.node_id,
            user_id=self.user.id if self.user else None,
            clock=self.clock,
            reason=reason,
            locked_before=self.locked_before,
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


def open_context(
    root: Path | None = None,
    clock: Clock = SYSTEM_CLOCK,
    training: bool | None = None,
    log_console: bool = False,
) -> AppContext:
    data_root = DataRoot(root or resolve_data_root()).ensure()
    setup_logging(data_root.logs, console=log_console)
    config = load_machine_config(data_root.config)
    use_training = config.training_mode if training is None else training
    db_path = data_root.training_db if use_training else data_root.local_db
    engine = create_sqlite_engine(db_path)
    upgrade(engine)
    factory = make_session_factory(engine)
    context = AppContext(data_root, config, engine, factory, clock=clock, training=use_training)
    with context.uow() as session:
        seed_defaults(session)
        register_node(session, config)
    from caspian_parking.services.fiscal import ensure_fiscal_year, locked_before

    with context.uow() as session:
        ensure_fiscal_year(session, clock.now_utc())
        context.locked_before = locked_before(session)
    log.info("opened %s database at %s", "training" if use_training else "local", db_path)
    return context
