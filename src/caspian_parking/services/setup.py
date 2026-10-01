"""First-run setup (SPEC §7): applies the wizard's choices — data root, role, gate, central server
(joining it for gates), devices, theme and the first administrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from caspian_parking.config.machine import (
    CameraConfig,
    MachineConfig,
    Role,
    load_machine_config,
    save_machine_config,
)
from caspian_parking.config.paths import DataRoot, write_data_root_pointer
from caspian_parking.config.secrets import SecretStore
from caspian_parking.core.clock import SYSTEM_CLOCK, Clock
from caspian_parking.data.repositories.system import GateRepository, UserRepository
from caspian_parking.services import auth
from caspian_parking.services.auth import MIN_PASSWORD_LENGTH
from caspian_parking.services.context import SQL_PASSWORD_SECRET, AppContext, open_context, server_engine_for

log = logging.getLogger(__name__)


class SetupError(RuntimeError):
    pass


@dataclass
class SetupChoices:
    data_root: Path
    role: Role = Role.STANDALONE
    gate_code: int = 1
    gate_name: str = ""
    server_host: str = ""
    server_database: str = "parking"
    server_driver: str = "ODBC Driver 18 for SQL Server"
    windows_auth: bool = True
    sql_user: str = ""
    sql_password: str = ""
    printer_backend: str = "simulator"
    printer_name: str = ""
    scanner_mode: str = "wedge"
    camera_kind: str = "off"  # off | simulator (cameras are set in detail later in Settings → Devices)
    theme: str = "dark"
    admin_username: str = ""
    admin_name: str = ""
    admin_password: str = ""
    admin_password_again: str = ""
    warnings: list[str] = field(default_factory=list)


def needs_wizard(ctx: AppContext) -> bool:
    """Only a fresh installation: no administrator yet and setup not done."""
    if ctx.config.first_run_done:
        return False
    with ctx.read() as session:
        if UserRepository(session).any_exists():
            ctx.config.first_run_done = True  # an installation from before the wizard existed
            ctx.save_config()
            return False
    return True


def validate(choices: SetupChoices, needs_admin: bool) -> None:
    if choices.role is not Role.STANDALONE and not choices.server_host.strip():
        raise SetupError("setup.server_required")
    if choices.role is not Role.SERVER and not 1 <= choices.gate_code <= 9:
        raise SetupError("setup.bad_gate_code")
    if needs_admin:
        if not choices.admin_username.strip():
            raise SetupError("setup.admin_required")
        if len(choices.admin_password) < MIN_PASSWORD_LENGTH:
            raise SetupError("password.too_short")
        if choices.admin_password != choices.admin_password_again:
            raise SetupError("user.password_mismatch")


def apply_setup(
    ctx: AppContext,
    choices: SetupChoices,
    server_engine: Engine | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> AppContext:
    """Write the configuration, join the server (gate), create the administrator; returns the new context.

    ``ctx`` is closed; the returned context is opened on the chosen data root.
    """
    with ctx.read() as session:
        needs_admin = not UserRepository(session).any_exists()
    validate(choices, needs_admin)
    root = DataRoot(choices.data_root).ensure()
    if root.root.resolve() != ctx.data_root.root.resolve():
        try:
            write_data_root_pointer(root.root)
        except OSError as exc:  # ProgramData not writable: the installer normally prepares it
            log.warning("cannot write the data root pointer: %s", exc)
            choices.warnings.append("setup.pointer_failed")
    config: MachineConfig = load_machine_config(root.config) if root.root != ctx.data_root.root else ctx.config
    config.node_id = ctx.config.node_id
    config.role = choices.role
    config.gate_code = None if choices.role is Role.SERVER else choices.gate_code
    config.server.host = choices.server_host.strip()
    config.server.database = choices.server_database.strip() or "parking"
    config.server.driver = choices.server_driver.strip()
    config.server.windows_auth = choices.windows_auth
    config.server.user = choices.sql_user.strip()
    config.devices.printer_backend = choices.printer_backend
    config.devices.printer_name = choices.printer_name
    config.devices.scanner_mode = choices.scanner_mode
    config.cameras = (
        [CameraConfig(lane=lane, kind=choices.camera_kind) for lane in ("entry", "exit")]
        if choices.camera_kind != "off"
        else []
    )
    config.theme_default = choices.theme
    secrets = SecretStore(root.config)
    if choices.sql_password:
        secrets.set(SQL_PASSWORD_SECRET, choices.sql_password)
    ctx.close()
    save_machine_config(root.config, config)
    if choices.role is Role.GATE:
        from caspian_parking.services.sync import join_server

        engine = server_engine or server_engine_for(config, secrets)
        try:
            join_server(root.root, engine, clock)
        finally:
            if server_engine is None:
                engine.dispose()
    if choices.role is Role.SERVER and server_engine is not None:
        new_ctx = open_context(root.root, clock=clock, engine=server_engine)
    elif choices.role is Role.GATE and server_engine is not None:
        new_ctx = open_context(root.root, clock=clock, server_engine=server_engine)
    else:
        new_ctx = open_context(root.root, clock=clock)
    with new_ctx.uow() as session:
        if choices.gate_name.strip() and new_ctx.config.gate_code is not None:
            gates = GateRepository(session)
            gate = gates.by_code(new_ctx.config.gate_code)
            if gate is not None and gate.name != choices.gate_name.strip():
                gates.update(gate, name=choices.gate_name.strip())
        if needs_admin and not UserRepository(session).any_exists():
            auth.create_user(
                session,
                choices.admin_username.strip(),
                choices.admin_name.strip() or choices.admin_username.strip(),
                choices.admin_password,
                preset="admin",
            )
    new_ctx.config.first_run_done = True
    new_ctx.save_config()
    return new_ctx
