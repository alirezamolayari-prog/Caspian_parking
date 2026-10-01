"""Phase 11: first-run wizard and setup (standalone, gate joining a server, server), validation."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from caspian_parking.config.machine import Role, load_machine_config
from caspian_parking.config.paths import resolve_data_root
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.data.repositories.system import GateRepository, UserRepository
from caspian_parking.services.setup import SetupChoices, SetupError, apply_setup, needs_wizard, validate
from caspian_parking.ui.shell.wizard import FirstRunWizard
from tests.services.test_sync import Site

MON = date(2026, 9, 28)


@pytest.fixture(autouse=True)
def program_data(tmp_path, monkeypatch):
    """The data-root pointer goes to a temporary ProgramData, never the real one."""
    folder = tmp_path / "ProgramData"
    folder.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(folder))
    monkeypatch.delenv("PARKING_DATA_ROOT", raising=False)
    return folder


def fill_admin(wizard: FirstRunWizard) -> None:
    wizard.admin_name.setText("مدیر")
    wizard.admin_username.setText("boss")
    wizard.admin_password.setText("secret1")
    wizard.admin_repeat.setText("secret1")


def test_needs_wizard_only_on_fresh_installs(app_ctx):
    assert needs_wizard(app_ctx)
    from caspian_parking.services import auth

    with app_ctx.uow() as session:
        auth.create_user(session, "old", "Old", "secret1", preset="admin")
    assert not needs_wizard(app_ctx)
    assert app_ctx.config.first_run_done


def test_standalone_wizard_flow(qtbot, app_ctx, themed, tmp_path, clock):
    wizard = FirstRunWizard(app_ctx)
    qtbot.addWidget(wizard)
    assert wizard.visible_pages() == ["welcome", "role", "devices", "admin", "done"]
    target = tmp_path / "ParkingData"
    wizard.data_root.setText(str(target))
    wizard.theme_buttons["light"].setChecked(True)
    wizard.next()
    assert wizard.current == "role"
    wizard.gate_code.setValue(2)
    wizard.gate_name.setText("درب شمالی")
    wizard.next()
    assert wizard.current == "devices"  # standalone: no server page
    wizard.camera_kind.setCurrentIndex(wizard.camera_kind.findData("simulator"))
    wizard.next()
    fill_admin(wizard)
    wizard.admin_repeat.setText("different")
    wizard.next()
    assert wizard.current == "done"
    assert "درب شمالی" in wizard.summary.text()
    wizard.next()
    assert wizard.error.text()  # passwords differ: not accepted
    assert wizard.result() != QDialog.DialogCode.Accepted
    wizard.back()
    assert wizard.current == "admin"
    wizard.admin_repeat.setText("secret1")
    wizard.next()
    wizard.next()
    assert wizard.result() == QDialog.DialogCode.Accepted
    ctx = apply_setup(app_ctx, wizard.choices(), clock=clock)
    try:
        assert ctx.data_root.root == target
        assert resolve_data_root() == target  # the pointer in ProgramData
        config = load_machine_config(target / "config")
        assert (config.role, config.gate_code, config.theme_default) == (Role.STANDALONE, 2, "light")
        assert config.first_run_done
        assert [c.kind for c in config.cameras] == ["simulator", "simulator"]
        with ctx.read() as session:
            assert UserRepository(session).by_username("boss") is not None
            assert GateRepository(session).by_code(2).name == "درب شمالی"
        assert not needs_wizard(ctx)
    finally:
        ctx.close()


def test_gate_role_joins_the_server(qtbot, tmp_path, clock, themed):
    clock.set(local_to_utc(datetime.combine(MON, time(10))))
    central = create_sqlite_engine(tmp_path / "central.db")
    site = Site(tmp_path, central, clock)
    try:
        from caspian_parking.config.machine import MachineConfig, save_machine_config
        from caspian_parking.config.paths import DataRoot
        from caspian_parking.services.context import open_context

        root = tmp_path / "newgate"
        save_machine_config(DataRoot(root).ensure().config, MachineConfig())
        fresh = open_context(root, clock=clock)
        wizard = FirstRunWizard(fresh)
        qtbot.addWidget(wizard)
        wizard.role_buttons[Role.GATE].setChecked(True)
        assert "server" in wizard.visible_pages()
        wizard.host.setText("central-sql")  # the engine is injected below
        wizard.gate_code.setValue(2)
        fill_admin(wizard)
        ctx = apply_setup(fresh, wizard.choices(), server_engine=central, clock=clock)
        try:
            assert ctx.config.role is Role.GATE
            assert ctx.config.joined_at
            with ctx.read() as session:
                assert UserRepository(session).by_username("boss") is not None  # from the server
        finally:
            ctx.close()
    finally:
        site.close()


def test_validation():
    base = SetupChoices(data_root=Path("x"))
    with pytest.raises(SetupError, match="server_required"):
        validate(SetupChoices(data_root=base.data_root, role=Role.GATE), needs_admin=False)
    with pytest.raises(SetupError, match="admin_required"):
        validate(base, needs_admin=True)
    with pytest.raises(SetupError, match="too_short"):
        validate(SetupChoices(data_root=base.data_root, admin_username="a", admin_password="1"), needs_admin=True)
    with pytest.raises(SetupError, match="bad_gate_code"):
        validate(SetupChoices(data_root=base.data_root, gate_code=12), needs_admin=False)
    validate(base, needs_admin=False)
