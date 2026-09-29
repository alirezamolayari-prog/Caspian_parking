from __future__ import annotations

from datetime import date, datetime, time

from caspian_parking.app import build_main_window
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.settings import get_setting
from caspian_parking.ui.screens.reports import preset_range

MON = date(2026, 9, 28)  # 1405/07/06


def at(hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(MON, time(hh, mm)))


def _traffic(ctx, clock):
    clock.set(at(10))
    gate = GateService(ctx)
    for plate in ("11ب111-11", "12ب111-11"):
        entry = gate.register_entry(parse_plate(plate))
        clock.advance(minutes=45)
        gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)


def test_preset_ranges():
    assert preset_range("today", MON) == (MON, MON)
    assert preset_range("yesterday", MON) == (date(2026, 9, 27), date(2026, 9, 27))
    first, last = preset_range("month", MON)
    assert first == date(2026, 9, 23)  # 1405/07/01
    assert last == MON
    first, last = preset_range("last_month", MON)
    assert (first, last) == (date(2026, 8, 23), date(2026, 9, 22))  # Shahrivar 1405 (31 days)
    assert preset_range("week", MON)[0] == date(2026, 9, 22)


def test_run_financial_report_in_background(qtbot, admin_ctx, clock):
    _traffic(admin_ctx, clock)
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("reports")
    screen.select_report("financial")
    screen.preset.setCurrentIndex(screen.preset.findData("today"))
    screen.run()
    qtbot.waitUntil(lambda: screen.result is not None, timeout=10_000)
    assert screen.result.totals["grand"] == 380_000
    assert screen.model.loaded_count() >= 5
    assert screen.kpis.count() >= 2
    excel = screen.export("excel", open_file=False)
    pdf = screen.export("pdf", open_file=False)
    assert excel.is_file()
    assert pdf.read_bytes().startswith(b"%PDF")


def test_occupancy_report_shows_heatmap(qtbot, admin_ctx, clock):
    _traffic(admin_ctx, clock)
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("reports")
    screen.select_report("occupancy")
    screen.run()
    qtbot.waitUntil(lambda: screen.result is not None, timeout=10_000)
    assert not screen.heatmap.isHidden()
    assert len(screen.result.chart["matrix"]) == 7


def test_plate_history_needs_text_field(qtbot, admin_ctx, clock):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("reports")
    screen.select_report("plate_history")
    assert not screen.text.isHidden()
    screen.select_report("financial")
    assert screen.text.isHidden()


def test_operator_sees_only_general_reports_and_no_export(qtbot, operator_ctx):
    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("reports")
    keys = {r.key for r in screen.reports}
    assert "financial" not in keys
    assert "occupancy" in keys
    assert all(b.isHidden() for b in screen.export_buttons)
    assert not hasattr(screen, "daily_enabled")


def test_daily_settings(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("reports")
    screen.daily_enabled.setChecked(False)
    screen.daily_folder.setText("D:/reports")
    screen.save_daily()
    with admin_ctx.read() as session:
        assert get_setting(session, "reports.daily_enabled") is False
        assert get_setting(session, "reports.daily_folder") == "D:/reports"


def test_dashboard(qtbot, admin_ctx, clock):
    _traffic(admin_ctx, clock)
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("dashboard")
    values = screen.kpis()
    assert values["entries"] == 2
    assert values["revenue"] == 380_000
    assert values["inside"] == 0
    qtbot.waitUntil(lambda: screen.heatmap_loaded, timeout=10_000)
    assert screen.recent_model.loaded_count() == 2
    assert screen.levels.count() >= 3
