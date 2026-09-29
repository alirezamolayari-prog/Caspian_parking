from __future__ import annotations

from datetime import date, datetime, time

from caspian_parking.app import build_main_window
from caspian_parking.config.machine import load_machine_config
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.services import backup
from caspian_parking.services.settings import get_setting


def _screen(qtbot, ctx, clock):
    clock.set(local_to_utc(datetime.combine(date(2026, 9, 28), time(10))))
    window = build_main_window(ctx)
    qtbot.addWidget(window)
    return window, window.show_screen("system")


def test_backup_tab(qtbot, admin_ctx, clock):
    window, screen = _screen(qtbot, admin_ctx, clock)
    assert "هنوز" in screen.backup_status.text()
    window.refresh_alerts()
    assert any(a.key == "backup" for a in window.alerts.alerts())
    results = screen.backup_now()
    assert len(results) == 1
    assert screen.backup_model.loaded_count() == 1
    window.refresh_alerts()
    assert not any(a.key == "backup" for a in window.alerts.alerts())
    screen.backup_table.selectRow(0)
    check = screen.test_selected()
    assert check is not None
    assert check.ok
    assert screen.restore_selected(ask=False)
    admin_ctx.engine.dispose()


def test_backup_settings(qtbot, admin_ctx, clock, tmp_path):
    _window, screen = _screen(qtbot, admin_ctx, clock)
    screen.backup_keep.setValue(7)
    screen.backup_on_close.setChecked(False)
    screen.backup_extra.setText(f"{tmp_path / 'usb'}; ")
    screen.save_backup_settings()
    with admin_ctx.read() as session:
        assert get_setting(session, "backup.keep") == 7
        assert get_setting(session, "backup.on_close") is False
    assert load_machine_config(admin_ctx.data_root.config).backup_destinations == [str(tmp_path / "usb")]
    assert len(backup.create_backup(admin_ctx)) == 2


def test_fiscal_tab(qtbot, admin_ctx, clock):
    _window, screen = _screen(qtbot, admin_ctx, clock)
    assert "۱۴۰۵" in screen.fiscal_info.text()
    assert screen.close_year(reason="x") is None  # confirmation text missing
    screen.fiscal_confirm.setText("1405")
    year = screen.close_year(reason="پایان سال")
    assert year.name == "1406"
    assert screen.fiscal_model.loaded_count() == 2


def test_storage_maintenance_training(qtbot, admin_ctx, clock):
    _window, screen = _screen(qtbot, admin_ctx, clock)
    assert "مگابایت" in screen.storage_info.text()
    screen.retention.setValue(30)
    screen.save_storage()
    with admin_ctx.read() as session:
        assert get_setting(session, "photos.retention_days") == 30
    assert screen.cleanup() == 0
    assert screen.run_maintenance()
    screen.training_toggle.setChecked(True)
    screen.save_training()
    assert load_machine_config(admin_ctx.data_root.config).training_mode


def test_operator_cannot_open_system(qtbot, operator_ctx):
    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    assert window.show_screen("system") is None
