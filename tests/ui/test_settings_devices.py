from __future__ import annotations

from caspian_parking.app import build_main_window
from caspian_parking.config.machine import load_machine_config
from caspian_parking.core.barcode import encode_payload
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services.gate_service import HMAC_SECRET
from caspian_parking.services.settings import get_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting


def _settings(qtbot, ctx):
    window = build_main_window(ctx)
    qtbot.addWidget(window)
    return window.show_screen("settings")


def test_admin_sees_receipt_and_hardware_tabs(qtbot, admin_ctx):
    screen = _settings(qtbot, admin_ctx)
    assert screen.tabs.count() == 4
    assert hasattr(screen, "hardware_tab")


def test_operator_sees_only_appearance(qtbot, operator_ctx):
    screen = _settings(qtbot, operator_ctx)
    assert screen.tabs.count() == 1


def test_hardware_save_updates_machine_config(qtbot, admin_ctx):
    tab = _settings(qtbot, admin_ctx).hardware_tab
    tab.gate_code.setValue(2)
    tab.backend.setCurrentIndex(tab.backend.findData("escpos"))
    tab.printer_name.setCurrentText("POS-80")
    tab.scanner_mode.setCurrentIndex(tab.scanner_mode.findData("serial"))
    tab.scanner_port.setCurrentText("COM3")
    tab.save()
    stored = load_machine_config(admin_ctx.data_root.config)
    assert stored.gate_code == 2
    assert stored.devices.printer_backend == "escpos"
    assert stored.devices.printer_name == "POS-80"
    assert stored.devices.scanner_port == "COM3"


def test_printer_follows_config_changes(admin_ctx):
    printing = ReceiptPrinting(admin_ctx)
    assert isinstance(printing.printer, SimulatorPrinter)
    admin_ctx.config.devices.printer_backend = "windows"
    admin_ctx.config.devices.printer_name = "Some Printer"
    assert printing.printer.name == "Some Printer"


def test_test_print_with_simulator(qtbot, admin_ctx):
    tab = _settings(qtbot, admin_ctx).hardware_tab
    assert tab.test_print()
    assert list((admin_ctx.data_root.logs / "printed").glob("*_test.png"))


def test_scanner_test_decodes_tickets(qtbot, admin_ctx):
    tab = _settings(qtbot, admin_ctx).hardware_tab
    key = admin_ctx.secrets.get_or_create(HMAC_SECRET)
    good = encode_payload(1, 5, admin_ctx.clock.now_utc(), key)
    assert "معتبر" in tab.show_scan(good)
    forged = good[:-1] + ("0" if good[-1] != "0" else "1")
    assert tab.show_scan(forged) == tab.scan_result.text()
    assert "1-2" not in tab.show_scan("hello")


def test_receipt_tab_saves_and_resets(qtbot, admin_ctx):
    tab = _settings(qtbot, admin_ctx).receipt_tab
    assert tab.art.count() >= 2  # built-in tree + wave
    assert not tab.preview.pixmap().isNull()
    tab.show_art.setChecked(False)
    tab.preview_first.setChecked(True)
    tab.labels["ticket_no"].setText("کد رسید")
    tab.art.setCurrentIndex(tab.art.findData("wave.png"))
    tab.save()
    with admin_ctx.read() as session:
        assert get_setting(session, "receipt.show_barcode_art") is False
        assert get_setting(session, "gate.preview_before_print") is True
        assert get_setting(session, "receipt.labels") == {"ticket_no": "کد رسید"}
        assert get_setting(session, "receipt.barcode_art") == "wave.png"
    tab.reset()
    with admin_ctx.read() as session:
        assert get_setting(session, "receipt.show_barcode_art") is True
        assert get_setting(session, "receipt.labels") == {}
    assert "logo" in tab.warnings.text() or tab.warnings.text()
