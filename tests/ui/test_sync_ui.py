"""Phase 9 UI: link indicator and alerts, review queue, other gate's ticket at the exit, local report banner,
server settings, morning report at login."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QDialog

from caspian_parking.app import build_main_window
from caspian_parking.config.machine import CameraConfig, Role, load_machine_config
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.devices.plate_source import SimulatorPlateSource
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.i18n import tr
from caspian_parking.services.camera import CameraService
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.reports import run_report
from caspian_parking.services.reports.base import range_params
from caspian_parking.services.sync import SyncEngine
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.screens import gate as gate_module
from caspian_parking.ui.screens.gate import GateScreen
from caspian_parking.ui.shell.morning import show_morning_report
from tests.services.test_sync import Site

MON = date(2026, 9, 28)
PLATE = parse_plate("12ب345-22")


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def site(tmp_path, clock, themed):
    clock.set(at(MON, 10))
    result = Site(tmp_path, create_sqlite_engine(tmp_path / "central.db"), clock)
    yield result
    result.close()


def gate_window(qtbot, site, ctx):
    window = build_main_window(ctx)
    qtbot.addWidget(window)
    assert window.sync is not None
    window.sync.stop()
    qtbot.waitUntil(lambda: not window.sync.busy, timeout=10_000)
    window.sync.engine = SyncEngine(ctx, site.server_engine, site.server_clock)
    return window


def test_link_indicator_and_alerts(qtbot, site, tmp_path):
    ctx = site.gate("gate1", 1)
    window = gate_window(qtbot, site, ctx)
    status = window.sync.sync_blocking()
    assert status.online
    assert ctx.link_online
    assert tr("link.connected") in window.topbar.link_chip.text()
    assert not any(a.key == "link" for a in window.alerts.alerts())
    window.sync.engine = SyncEngine(ctx, create_sqlite_engine(tmp_path / "nope" / "x" / "db.db"), site.server_clock)
    GateService(ctx).register_entry(PLATE)
    offline = window.sync.sync_blocking()
    assert not offline.online
    assert not ctx.link_online
    assert tr("link.disconnected") in window.topbar.link_chip.text()
    assert any(a.key == "link" for a in window.alerts.alerts())
    window.sync.engine = SyncEngine(ctx, site.server_engine, lambda _c: site.clock.now_utc() + timedelta(minutes=3))
    window.sync.sync_blocking()
    assert not any(a.key == "link" for a in window.alerts.alerts())
    assert any(a.key == "clock" for a in window.alerts.alerts())


def test_report_source_follows_the_link(site):
    ctx = site.gate("gate1", 1)
    ctx.link_online = False
    assert run_report(ctx, "open_sessions", range_params(MON, MON)).local_only
    ctx.link_online = True
    assert not run_report(ctx, "open_sessions", range_params(MON, MON)).local_only


def test_review_queue_cancel_duplicate(qtbot, site):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    person = PeopleService(g1).create_person(PersonInput(first_name="علی", plates=[(PLATE, None)]))
    site.sync(g1)
    site.sync(g2)
    PeopleService(g1).pay_subscription(person.id, "cash")
    PeopleService(g2).pay_subscription(person.id, "cash")
    for ctx in (g1, g2, g1):
        site.sync(ctx)
    window = gate_window(qtbot, site, g1)
    screen = window.show_screen("review")
    assert screen.items_model.loaded_count() == 1
    screen.items_table.selectRow(0)
    screen.show_item(screen.items_table.selected_object())
    assert screen.rows_model.loaded_count() == 2
    assert screen.cancel_button.isVisibleTo(screen)
    screen.rows_table.selectRow(1)
    assert screen.cancel_selected(reason="تکراری")
    assert screen.rows_model.row_object(1).cancelled
    assert not screen.resolve()  # resolution text is required
    screen.resolution.setText("یکی باطل شد")
    assert screen.resolve()
    assert screen.items_model.loaded_count() == 0
    assert screen.empty.isVisibleTo(screen)


def test_other_gates_ticket_at_exit_while_offline(qtbot, site, clock, monkeypatch):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    entry = GateService(g1).register_entry(PLATE)
    clock.advance(minutes=40)
    screen = GateScreen(g2, ReceiptPrinting(g2, SimulatorPrinter()))
    qtbot.addWidget(screen)
    monkeypatch.setattr(gate_module.ForeignTicketDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    screen.ticket_field.setText(entry.payload)
    quote = screen.calculate()
    assert quote is not None
    assert quote.breakdown.total_minutes == 40
    assert screen.pay(PaymentMethod.CASH)
    # typed ticket number of the other gate: the entry time is typed from the ticket
    second = GateService(g1).register_entry(parse_plate("55ج777-11"))
    screen.ticket_field.setText(str(second.ticket))
    dialog_values = {}

    def fake_exec(dialog):
        dialog_values["enabled"] = dialog.date.isEnabled()
        dialog.date.set_gregorian(MON)  # the operator types the entry time printed on the ticket
        dialog.time.setTime(QTime(10, 40))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(gate_module.ForeignTicketDialog, "exec", fake_exec)
    assert screen.calculate() is not None
    assert dialog_values["enabled"]  # no barcode: the operator must type the time


def test_server_settings_tab(qtbot, site):
    from caspian_parking.ui.screens.settings_server import ServerTab

    tab = ServerTab(site.server)
    qtbot.addWidget(tab)
    tab.host.setText("")
    assert not tab.test_connection()
    assert tr("server.no_host") in tab.status.text()
    tab.update_share.setText(r"\\server\updates")
    tab.role.setCurrentIndex(tab.role.findData("server"))
    tab.save()
    config = load_machine_config(site.server.data_root.config)
    assert config.role is Role.SERVER
    from caspian_parking.services.settings import get_setting

    with site.server.read() as session:
        assert get_setting(session, "update.share") == r"\\server\updates"


@pytest.mark.mssql
def test_server_settings_join_on_localdb(qtbot, tmp_path, clock, themed):
    from caspian_parking.config.machine import MachineConfig, save_machine_config
    from caspian_parking.config.paths import DataRoot
    from caspian_parking.services import auth
    from caspian_parking.services.auth import CurrentUser
    from caspian_parking.services.context import open_context
    from caspian_parking.ui.screens.settings_server import ServerTab
    from tests.support.mssql import available_driver, localdb_pipe, temporary_database, unavailable_reason

    if unavailable_reason():
        pytest.skip(unavailable_reason())
    clock.set(at(MON, 10))
    with temporary_database() as engine:
        site = Site(tmp_path, engine, clock)
        try:
            database = engine.url.query["odbc_connect"].split("DATABASE=")[1].split(";")[0]
            root = tmp_path / "newgate"
            save_machine_config(DataRoot(root).ensure().config, MachineConfig(gate_code=2))
            ctx = open_context(root, clock=clock)
            with ctx.uow() as session:
                user = auth.create_user(session, "installer", "I", "secret1", preset="admin")
            ctx.user = CurrentUser.from_user(user)
            tab = ServerTab(ctx)
            qtbot.addWidget(tab)
            tab.host.setText(localdb_pipe())
            tab.database.setText(database)
            tab.driver.setCurrentText(available_driver())
            tab.windows_auth.setChecked(True)
            assert tab.test_connection()
            assert tab.join(ask=False)
            ctx.close()
            config = load_machine_config(root / "config")
            assert config.role is Role.GATE
            assert config.joined_at
        finally:
            site.close()


def test_morning_report_at_login(qapp, site, clock):
    ctx = site.gate("gate1", 1)
    camera = SimulatorPlateSource(CameraConfig(lane="entry", kind="simulator"), clock)
    clock.set(at(MON, 23))
    CameraService(ctx).record_pass(camera.simulate(PLATE.key), after_hours=True)
    clock.set(at(MON + timedelta(days=1), 10))
    ack = show_morning_report(None, ctx, ask=False)
    assert ack is not None
    assert ack.passes == 1
    assert show_morning_report(None, ctx, ask=False) is None
