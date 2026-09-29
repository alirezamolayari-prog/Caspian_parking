"""Main operator flows with simulators: entry → print (preview) → exit → payment (CLAUDE.md verification)."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from sqlalchemy import select

from caspian_parking.app import build_main_window
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import PlateKind, parse_plate
from caspian_parking.core.tariff import PassThroughType
from caspian_parking.data.models import Payment, Visit
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services.gate_service import PaymentMethod
from caspian_parking.services.settings import set_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.screens.gate import GateScreen
from caspian_parking.ui.screens.gate_dialogs import AlreadyInsideDialog, LostTicketDialog, ReceiptPreviewDialog

MON = date(2026, 9, 28)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def screen(qtbot, admin_ctx, clock):
    clock.set(at(MON, 10))
    printer = SimulatorPrinter()
    widget = GateScreen(admin_ctx, ReceiptPrinting(admin_ctx, printer))
    qtbot.addWidget(widget)
    widget.resize(1500, 900)
    widget.show()
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow, timeout=2000)
    return widget


def type_plate(screen: GateScreen, text: str) -> None:
    assert screen.plate_input.set_text(text)


def test_entry_prints_receipt_and_lists_vehicle(screen):
    type_plate(screen, "12ب345-22")
    assert screen.entry_preview.plate() == parse_plate("12ب345-22")
    result = screen.print_entry()
    assert result is not None
    printer = screen.printing.printer
    assert len(printer.printed) == 1
    assert printer.printed[0].width() == 576
    assert screen.inside_model.loaded_count() == 1
    assert screen.plate_input.plate() is None  # cleared for the next car
    assert screen.today_model.loaded_count() == 1


def test_enter_in_plate_boxes_prints(screen, qtbot):
    type_plate(screen, "12ب345-22")
    screen.plate_input.region.setFocus()
    qtbot.keyClick(screen.plate_input.region, Qt.Key.Key_Return)
    assert len(screen.printing.printer.printed) == 1


def test_incomplete_plate_is_not_printed(screen):
    screen.plate_input.left.setText("12")
    assert screen.print_entry() is None
    assert screen.printing.printer.printed == []


def test_full_flow_entry_exit_payment(screen, admin_ctx, clock):
    type_plate(screen, "12ب345-22")
    entry = screen.print_entry()
    clock.advance(minutes=73)
    screen.on_scanned(entry.payload)  # scanner → exit lane
    assert screen.quote is not None
    assert screen.quote.amount_due == 240_000
    assert screen.exit_details.isVisibleTo(screen)
    assert screen.pay(PaymentMethod.CARD)
    with admin_ctx.read() as session:
        payment = session.scalars(select(Payment)).one()
        assert (payment.method, payment.amount) == ("card", 240_000)
    assert screen.inside_model.loaded_count() == 0
    assert screen.quote is None


def test_exit_by_typed_ticket_number_and_exit_receipt(screen, clock):
    entry = screen._register(parse_plate("55ج777-11"), *_defaults())
    clock.advance(minutes=30)
    screen.ticket_field.setText(str(entry.ticket))
    assert screen.calculate().amount_due == 190_000
    screen.exit_receipt.setChecked(True)
    assert screen.pay(PaymentMethod.CASH)
    assert len(screen.printing.printer.printed) == 2  # entry + exit receipt


def _defaults():
    from caspian_parking.core.tariff import VehicleType, VisitKind

    return VehicleType.SEDAN, VisitKind.TRANSIENT


def test_manual_amount_needs_reason(screen, admin_ctx, clock):
    type_plate(screen, "12ب345-22")
    entry = screen.print_entry()
    clock.advance(minutes=100)
    screen.on_scanned(entry.payload)
    screen.manual_amount.set_value(50_000)
    assert not screen.pay(PaymentMethod.CASH)  # no reason
    screen.adjust_reason.setText("مشتری ویژه")
    assert screen.pay(PaymentMethod.CASH)
    with admin_ctx.read() as session:
        assert session.scalars(select(Visit)).one().amount_paid == 50_000


def test_preview_before_print(screen, admin_ctx, monkeypatch):
    with admin_ctx.uow() as session:
        set_setting(session, "gate.preview_before_print", True)
    shown = []

    def fake_exec(dialog):
        shown.append(dialog)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReceiptPreviewDialog, "exec", fake_exec)
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    assert len(shown) == 1
    assert len(screen.printing.printer.printed) == 1


def test_already_inside_offers_exit(screen, monkeypatch):
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    monkeypatch.setattr(AlreadyInsideDialog, "exec", lambda self: AlreadyInsideDialog.EXIT)
    type_plate(screen, "12ب345-22")
    assert screen.print_entry() is None
    assert screen.quote is not None
    monkeypatch.setattr(AlreadyInsideDialog, "exec", lambda self: AlreadyInsideDialog.DUPLICATE)
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    assert len(screen.printing.printer.printed) == 2  # original + duplicate


def test_no_plate_motorcycle_and_pass_through(screen):
    assert screen.print_without_plate().session.no_plate
    screen.toggle_motorcycle()
    assert screen.plate_input.mode() is PlateKind.MOTORCYCLE
    type_plate(screen, "123-45678")
    assert screen.print_entry().session.vehicle_type == "motorcycle"
    assert screen.plate_input.mode() is PlateKind.CAR  # back to cars after printing
    type_plate(screen, "55ج777-11")
    result = screen.print_pass_through(PassThroughType.TAXI)
    assert result.session.kind == "pass_through"
    assert result.session.pass_type == "taxi"
    menu = screen.open_pass_menu()
    assert len(menu.actions()) == 4
    menu.close()


def test_flee_debt_banner_and_collection(screen, admin_ctx, clock, monkeypatch):
    type_plate(screen, "12ب345-22")
    entry = screen.print_entry()
    clock.advance(minutes=60)
    screen.on_scanned(entry.payload)
    monkeypatch.setattr("caspian_parking.ui.screens.gate.confirm", lambda *a, **k: True)
    assert screen.flee_current()
    clock.advance(days=1)
    type_plate(screen, "12ب345-22")
    assert screen.entry_banner.isVisibleTo(screen)
    assert screen.collect_debts(PaymentMethod.CASH)
    assert not screen.entry_banner.isVisibleTo(screen)
    with admin_ctx.read() as session:
        assert session.scalars(select(Visit)).one().status == "recovered"


def test_cancel_entry_with_reason(screen):
    type_plate(screen, "12ب345-22")
    entry = screen.print_entry()
    screen.on_scanned(entry.payload)
    assert screen.cancel_current(reason="اشتباه")
    assert screen.inside_model.loaded_count() == 0


def test_lost_ticket_dialog_flows(screen, qtbot, monkeypatch):
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    dialog = LostTicketDialog(screen, screen.gate)
    qtbot.addWidget(dialog)
    assert dialog.results.count() == 1
    dialog.search.setText("999")
    assert dialog.results.count() == 0
    monkeypatch.setattr(LostTicketDialog, "exec", lambda self: LostTicketDialog.COLLECT)
    screen.lost_ticket_flow()
    assert screen.lost_ticket
    assert screen.quote is not None


def test_night_mark_from_inside_list(screen):
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    screen.inside_table.selectRow(0)
    assert screen.mark_night_current()
    assert "شب" in screen.inside_model.data(screen.inside_model.index(0, 3))


def test_printer_failure_raises_alert(qtbot, admin_ctx, clock):
    clock.set(at(MON, 10))
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    gate = window.show_screen("gate")
    gate.printing.printer = SimulatorPrinter()
    gate.printing.printer.paper_ok = False
    gate.plate_input.set_text("12ب345-22")
    assert gate.print_entry() is not None  # the entry is registered even if printing fails
    assert any(a.key == "printer" for a in window.alerts.alerts())


def test_fkeys_and_space(screen, qtbot):
    type_plate(screen, "12ب345-22")
    screen.print_button.setFocus()
    qtbot.keyClick(screen, Qt.Key.Key_Space)
    assert len(screen.printing.printer.printed) == 1
    screen.ticket_field.setFocus()
    qtbot.keyClick(screen.ticket_field, Qt.Key.Key_F2)
    assert len(screen.printing.printer.printed) == 2  # F2 = receipt without plate


def test_operator_does_not_see_adjustments(qtbot, operator_ctx, clock):
    clock.set(at(MON, 10))
    screen = GateScreen(operator_ctx, ReceiptPrinting(operator_ctx, SimulatorPrinter()))
    qtbot.addWidget(screen)
    assert not screen.adjust_box.isVisibleTo(screen)
    assert not screen.cancel_button.isVisibleTo(screen)


def test_gate_screen_is_first_for_operators(qtbot, operator_ctx):
    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    assert window.screens[0].key == "gate"
    assert window.current_key() == "gate"


def test_status_strip_counts(screen, clock):
    for index in range(3):
        type_plate(screen, f"1{index}ب345-22")
        screen.print_entry()
    assert "۳" in screen.counter_chips["total"].text()
    assert screen.levels_box.count() == 1


def test_scanner_ignored_when_hidden(screen):
    screen.hide()
    screen.on_scanned("12345678901234567890")
    assert screen.quote is None


def test_night_list_is_printed(screen):
    for index in range(2):
        type_plate(screen, f"1{index}ب345-22")
        screen.print_entry()
    assert screen.print_night_list()
    image = screen.printing.printer.printed[-1]
    assert image.width() == 576
    image.save(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "artifacts" / "receipt_night_list.png"))


def test_overnight_vehicles_flagged_on_show(screen, clock):
    type_plate(screen, "12ب345-22")
    screen.print_entry()
    clock.advance(days=1)
    screen.on_show()
    assert "شب" in screen.inside_model.data(screen.inside_model.index(0, 3))


def test_scanner_mode_from_config(qtbot, admin_ctx):
    from caspian_parking.devices.scanner import SerialScanner

    admin_ctx.config.devices.scanner_mode = "off"
    off = GateScreen(admin_ctx, ReceiptPrinting(admin_ctx, SimulatorPrinter()))
    qtbot.addWidget(off)
    assert off.scanner is None
    admin_ctx.config.devices.scanner_mode = "serial"
    admin_ctx.config.devices.scanner_port = "COM_NONE"
    serial = GateScreen(admin_ctx, ReceiptPrinting(admin_ctx, SimulatorPrinter()))
    qtbot.addWidget(serial)
    assert isinstance(serial.scanner, SerialScanner)
    serial.scanner.stop()
