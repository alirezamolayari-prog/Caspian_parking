from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QTime

from caspian_parking.app import build_main_window
from caspian_parking.core.jalali import JalaliDate, local_to_utc
from caspian_parking.core.permissions import Permission
from caspian_parking.core.tariff import PriceBasis, VehicleType
from caspian_parking.data.repositories.tariff import HolidayRepository, TariffVersionRepository
from caspian_parking.i18n.bidi import strip_isolates
from caspian_parking.services import tariff_service as ts
from caspian_parking.ui.widgets.inputs import JalaliDateEdit, MoneyField

MON = date(2026, 9, 28)


def test_money_field(qtbot, themed):
    field = MoneyField(190_000)
    qtbot.addWidget(field)
    assert field.text() == "۱۹۰٬۰۰۰"
    field.setText("2500000")
    field.textEdited.emit(field.text())
    assert field.value() == 2_500_000
    assert field.text() == "۲٬۵۰۰٬۰۰۰"
    field.setText("")
    field.textEdited.emit("")
    assert field.value() == 0
    qtbot.keyClicks(field, "12")
    assert field.value() == 12


def test_jalali_date_edit(qtbot, themed):
    edit = JalaliDateEdit(JalaliDate(1405, 7, 6))
    qtbot.addWidget(edit)
    changes = []
    edit.date_changed.connect(changes.append)
    assert edit.field.text() == "۱۴۰۵/۰۷/۰۶"
    assert edit.gregorian() == MON
    edit.field.setText("1405/7/10")
    edit.field.editingFinished.emit()
    assert edit.value() == JalaliDate(1405, 7, 10)
    edit.field.setText("1405/13/40")
    edit.field.editingFinished.emit()
    assert edit.field.property("invalid") == "true"
    assert edit.value() == JalaliDate(1405, 7, 10)
    edit.shift_days(-4)
    assert edit.gregorian() == MON
    popup = edit.open_popup()
    popup.next_month()
    assert popup.month == 8
    popup.prev_month()
    popup.prev_month()
    assert popup.month == 6
    popup.next_month()
    # Mehr 1405 starts on a Wednesday (Persian index 4): day 1 is button #4
    popup._clicked(4 + 14)
    assert edit.value() == JalaliDate(1405, 7, 15)
    assert len(changes) >= 3


def test_calendar_year_wrap(qtbot, themed):
    edit = JalaliDateEdit(JalaliDate(1405, 12, 1))
    qtbot.addWidget(edit)
    popup = edit.open_popup()
    popup.next_month()
    assert (popup.year, popup.month) == (1406, 1)
    popup.prev_month()
    assert (popup.year, popup.month) == (1405, 12)


def _screen(qtbot, ctx):
    window = build_main_window(ctx)
    qtbot.addWidget(window)
    return window.show_screen("tariffs")


def test_tariffs_screen_visibility(qtbot, operator_ctx):
    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    assert window.show_screen("tariffs") is None
    assert not operator_ctx.can(Permission.CHANGE_TARIFFS)


def test_tariff_form_loads_current_values(qtbot, admin_ctx):
    screen = _screen(qtbot, admin_ctx)
    assert screen.money["entry_fee"].value() == 190_000
    assert screen.minutes["pass_through_free_minutes"].value() == 20
    assert screen.moto_night.isChecked()
    assert screen.basis_buttons[PriceBasis.ENTRY].isChecked()
    assert screen.history_model.loaded_count() == 1


def test_save_new_tariff_version(qtbot, admin_ctx, clock):
    clock.set(local_to_utc(datetime.combine(MON, time(9, 0))))
    screen = _screen(qtbot, admin_ctx)
    assert not screen.save_version(ask=False)  # nothing changed
    screen.money["entry_fee"].set_value(250_000)
    screen.effective_date.set_gregorian(MON)
    screen.effective_time.setTime(QTime(12, 0))
    screen.note.setText("افزایش")
    assert screen.save_version(ask=False)
    with admin_ctx.read() as session:
        history = TariffVersionRepository(session).history()
        assert history[0].amounts["entry_fee"] == 250_000
        assert history[0].note == "افزایش"
    assert screen.history_model.loaded_count() == 2
    # retroactive dates are refused
    screen.money["entry_fee"].set_value(300_000)
    screen.effective_time.setTime(QTime(8, 0))
    assert not screen.save_version(ask=False)
    # the future version can be withdrawn
    screen.history.selectRow(0)
    assert screen.withdraw_selected()
    with admin_ctx.read() as session:
        assert ts.load_schedule(session).at(local_to_utc(datetime.combine(MON, time(13)))).entry_fee == 190_000


def test_change_price_basis_only(qtbot, admin_ctx):
    screen = _screen(qtbot, admin_ctx)
    screen.basis_buttons[PriceBasis.EXIT].setChecked(True)
    assert screen.save_version(ask=False)
    with admin_ctx.read() as session:
        assert ts.load_price_basis(session) is PriceBasis.EXIT
        assert TariffVersionRepository(session).count() == 1


def test_hours_tab_saves_calendar(qtbot, admin_ctx):
    screen = _screen(qtbot, admin_ctx)
    _is_open, opens, closes, _free = screen.day_rows[MON.weekday()]
    opens.setTime(QTime(10, 0))
    closes.setTime(QTime(22, 0))
    screen.day_rows[4][0].setChecked(False)  # Friday closed
    screen.day_rows[3][3].setChecked(False)  # Thursday no longer free
    assert screen.save_hours()
    with admin_ctx.read() as session:
        calendar = ts.load_calendar(session)
    assert calendar.hours_for(MON).open == time(10)
    assert calendar.hours[4] is None
    assert calendar.free_weekdays == frozenset({4})
    closes.setTime(QTime(9, 0))
    assert not screen.save_hours()


def test_holidays_tab(qtbot, admin_ctx):
    screen = _screen(qtbot, admin_ctx)
    assert not screen.holiday_empty.isHidden()  # page is in a background tab
    screen.holiday_date.set_gregorian(MON)
    assert not screen.add_holiday()  # title required
    screen.holiday_title.setText("تعطیل رسمی")
    assert screen.add_holiday()
    assert screen.holiday_model.loaded_count() == 1
    screen.holiday_table.selectRow(0)
    assert screen.remove_holiday()
    with admin_ctx.read() as session:
        assert HolidayRepository(session).upcoming() == []


def test_calculator_matches_spec_example(qtbot, admin_ctx):
    screen = _screen(qtbot, admin_ctx)
    wednesday = date(2026, 9, 30)
    screen.calc_entry_date.set_gregorian(wednesday)
    screen.calc_entry_time.setTime(QTime(14, 0))
    screen.calc_exit_date.set_gregorian(wednesday + timedelta(days=1))
    screen.calc_exit_time.setTime(QTime(11, 0))
    result = screen.calculate()
    assert result.total == 3_240_000
    assert "۳٬۲۴۰٬۰۰۰" in strip_isolates(screen.result_total.text())
    assert not screen.result_card.isHidden()
    screen.calc_vehicle.setCurrentIndex(screen.calc_vehicle.findData(VehicleType.MOTORCYCLE.value))
    assert screen.calculate().total == 2_200_000
    screen.calc_exit_date.set_gregorian(date(2026, 9, 1))
    assert screen.calculate() is None


def test_time_field_keeps_ltr_order_and_parses_any_digits(qtbot, themed):
    from caspian_parking.ui.widgets.inputs import TimeField, parse_time_text

    field = TimeField(QTime(9, 30))
    qtbot.addWidget(field)
    assert field.text() == "۰۹:۳۰"
    field.setText("۲۰:۴۵")
    field.editingFinished.emit()
    assert field.time() == QTime(20, 45)
    field.setText("25:99")
    field.editingFinished.emit()
    assert field.property("invalid") == "true"
    assert field.time() == QTime(20, 45)
    assert parse_time_text("930") == QTime(9, 30)
    assert parse_time_text("٠٩٣٠") == QTime(9, 30)
    assert parse_time_text("7") == QTime(7, 0)
    assert parse_time_text("12345") is None
    assert parse_time_text(":") is None
