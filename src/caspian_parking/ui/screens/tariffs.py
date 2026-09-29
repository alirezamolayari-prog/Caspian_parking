"""Tariffs, working hours, free days, holidays and a price calculator (SPEC §4.5 settings)."""

from __future__ import annotations

from datetime import datetime, time

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.jalali import local_to_utc, to_local
from caspian_parking.core.tariff import (
    DayHours,
    PriceBasis,
    PriceBreakdown,
    TariffError,
    TariffValues,
    VehicleType,
    VisitKind,
)
from caspian_parking.data.models import Holiday, TariffVersionRecord
from caspian_parking.data.repositories.tariff import HolidayRepository, TariffVersionRepository
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date, fa_datetime, fa_digits, fa_duration, fa_money, weekday_name
from caspian_parking.services import tariff_service as ts
from caspian_parking.services.context import AppContext
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import EmptyState, confirm, show_toast
from caspian_parking.ui.widgets.inputs import JalaliDateEdit, MoneyField, TimeField, time_edit
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

MONEY_FIELDS = ("entry_fee", "hourly_rate", "rounding_step", "motorcycle_flat", "night_fine", "subscription_price")
MINUTE_FIELDS = ("entry_minutes", "night_fine_grace_minutes", "pass_through_free_minutes", "subscription_days")
PERSIAN_WEEK = (5, 6, 0, 1, 2, 3, 4)  # Python weekdays in Persian order: Saturday … Friday


def _qtime(value: time) -> QTime:
    return QTime(value.hour, value.minute)


def _pytime(value: QTime) -> time:
    return time(value.hour(), value.minute())


class TariffsScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("tariffs.title"), tr("tariffs.subtitle"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._tariff_tab()), tr("tariffs.tab_tariff"))
        self.tabs.addTab(self._scroll(self._hours_tab()), tr("tariffs.tab_hours"))
        self.tabs.addTab(self._holidays_tab(), tr("tariffs.tab_holidays"))
        self.tabs.addTab(self._scroll(self._calculator_tab()), tr("tariffs.tab_calculator"))
        self.body.addWidget(self.tabs, 1)
        self.reload()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def on_show(self) -> None:
        self.reload()

    # ================================================================ tariff tab
    def _tariff_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)
        form_card = Card(tr("tariffs.current"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.S)
        self.money: dict[str, MoneyField] = {}
        self.minutes: dict[str, QSpinBox] = {}
        row = 0
        for index, name in enumerate(MONEY_FIELDS):
            field = MoneyField()
            self.money[name] = field
            grid.addWidget(label(tr(f"tariff.{name}"), "caption"), row + (index // 2) * 2, index % 2)
            grid.addWidget(field, row + (index // 2) * 2 + 1, index % 2)
        row = ((len(MONEY_FIELDS) + 1) // 2) * 2
        for index, name in enumerate(MINUTE_FIELDS):
            spin = QSpinBox()
            spin.setRange(1 if name in {"entry_minutes", "subscription_days"} else 0, 100_000)
            spin.setSuffix(" " + tr("tariff.unit_days" if name == "subscription_days" else "tariff.unit_minutes"))
            self.minutes[name] = spin
            grid.addWidget(label(tr(f"tariff.{name}"), "caption"), row + (index // 2) * 2, index % 2)
            grid.addWidget(spin, row + (index // 2) * 2 + 1, index % 2)
        row += ((len(MINUTE_FIELDS) + 1) // 2) * 2
        self.moto_night = QCheckBox(tr("tariff.motorcycle_night_fine"))
        grid.addWidget(self.moto_night, row, 0, 1, 2)
        form_card.body().addLayout(grid)
        form_card.add(label(tr("tariffs.basis"), "title"))
        self.basis_group = QButtonGroup(self)
        basis_row = QHBoxLayout()
        self.basis_buttons: dict[PriceBasis, QRadioButton] = {}
        for basis in PriceBasis:
            radio = QRadioButton(tr(f"tariffs.basis_{basis.value}"))
            self.basis_group.addButton(radio)
            self.basis_buttons[basis] = radio
            basis_row.addWidget(radio)
        basis_row.addStretch(1)
        form_card.body().addLayout(basis_row)
        form_card.add(label(tr("tariffs.effective_from"), "title"))
        when = QHBoxLayout()
        self.effective_date = JalaliDateEdit()
        self.effective_time = time_edit()
        when.addWidget(self.effective_date, 2)
        when.addWidget(self.effective_time, 1)
        self.note = TextField(tr("tariffs.note"))
        when.addWidget(self.note, 3)
        form_card.body().addLayout(when)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_version_button = Button(
            tr("tariffs.save_version"), "check", variant="primary", on_click=self.save_version
        )
        actions.addWidget(self.save_version_button)
        form_card.body().addLayout(actions)
        layout.addWidget(form_card)

        history_card = Card(tr("tariffs.history"))
        columns = [
            Column(tr("tariffs.col_from"), lambda r: fa_datetime(r.effective_from_utc), width=170),
            Column(tr("tariff.entry_fee"), lambda r: fa_money(r.amounts["entry_fee"], False), width=120),
            Column(tr("tariff.hourly_rate"), lambda r: fa_money(r.amounts["hourly_rate"], False), width=120),
            Column(tr("tariff.night_fine"), lambda r: fa_money(r.amounts["night_fine"], False), width=120),
            Column(tr("tariffs.col_status"), self._status_text, width=110),
            Column(tr("tariffs.note"), lambda r: r.note if r.note and r.note != "seed" else "—"),
        ]
        self.history_model = LazyTableModel(columns, self._fetch_history)
        self.history = DataTable(self.history_model)
        self.history.setMinimumHeight(220)
        history_card.add(self.history)
        withdraw_row = QHBoxLayout()
        withdraw_row.addStretch(1)
        withdraw_row.addWidget(
            Button(tr("tariffs.withdraw"), "rotate-ccw", variant="ghost", on_click=self.withdraw_selected)
        )
        history_card.body().addLayout(withdraw_row)
        layout.addWidget(history_card)
        return page

    def _fetch_history(self, offset: int, limit: int) -> list[TariffVersionRecord]:
        with self.ctx.read() as session:
            return TariffVersionRepository(session).history()[offset : offset + limit]

    def _status_text(self, record: TariffVersionRecord) -> str:
        if not record.is_active:
            return tr("tariffs.status_withdrawn")
        now = self.ctx.clock.now_utc()
        if record.effective_from_utc > now:
            return tr("tariffs.status_future")
        with self.ctx.read() as session:
            current = ts.load_schedule(session).version_at(now)
        return (
            tr("tariffs.status_current")
            if current.effective_from == record.effective_from_utc
            else tr("tariffs.status_past")
        )

    def current_values(self) -> TariffValues:
        with self.ctx.read() as session:
            return ts.load_schedule(session).at(self.ctx.clock.now_utc())

    def form_values(self) -> TariffValues:
        data: dict[str, object] = {name: field.value() for name, field in self.money.items()}
        data.update({name: spin.value() for name, spin in self.minutes.items()})
        data["motorcycle_night_fine"] = self.moto_night.isChecked()
        return TariffValues.from_dict(data)

    def selected_basis(self) -> PriceBasis:
        for basis, radio in self.basis_buttons.items():
            if radio.isChecked():
                return basis
        return PriceBasis.ENTRY

    def effective_from(self) -> datetime:
        day = self.effective_date.gregorian()
        return local_to_utc(datetime.combine(day, _pytime(self.effective_time.time())))

    def save_version(self, ask: bool = True) -> bool:
        values = self.form_values()
        basis = self.selected_basis()
        with self.ctx.read() as session:
            current_basis = ts.load_price_basis(session)
        values_changed = values != self.current_values()
        if not values_changed and basis == current_basis:
            show_toast(self, tr("tariffs.no_change"), "info")
            return False
        if ask and not confirm(self, tr("tariffs.confirm_title"), tr("tariffs.confirm_body")):
            return False
        try:
            with self.ctx.uow(reason=self.note.value() or None) as session:
                if values_changed:
                    ts.add_tariff_version(
                        session, values, self.effective_from(), self.ctx.clock.now_utc(), self.note.value() or None
                    )
                if basis != current_basis:
                    ts.save_price_basis(session, basis)
        except TariffError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("common.saved"))
        self.reload()
        return True

    def withdraw_selected(self) -> bool:
        record = self.history.selected_object()
        if record is None:
            show_toast(self, tr("tariffs.select_version"), "info")
            return False
        try:
            with self.ctx.uow() as session:
                stored = session.get(TariffVersionRecord, record.id)
                assert stored is not None
                ts.cancel_tariff_version(session, stored, self.ctx.clock.now_utc(), tr("tariffs.withdraw"))
        except TariffError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("common.saved"))
        self.reload()
        return True

    # ================================================================ hours tab
    def _hours_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        card = Card(tr("tariffs.hours_title"))
        card.add(label(tr("tariffs.hours_hint"), "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.S)
        for column, key in enumerate(
            ("tariffs.day", "tariffs.open_day", "tariffs.opens", "tariffs.closes", "tariffs.free")
        ):
            grid.addWidget(label(tr(key), "caption"), 0, column)
        self.day_rows: dict[int, tuple[QCheckBox, TimeField, TimeField, QCheckBox]] = {}
        for row, weekday in enumerate(PERSIAN_WEEK, start=1):
            is_open = QCheckBox()
            opens = time_edit()
            closes = time_edit()
            free = QCheckBox()
            is_open.toggled.connect(lambda checked, o=opens, c=closes: (o.setEnabled(checked), c.setEnabled(checked)))
            grid.addWidget(label(weekday_name(weekday), "title"), row, 0)
            grid.addWidget(is_open, row, 1)
            grid.addWidget(opens, row, 2)
            grid.addWidget(closes, row, 3)
            grid.addWidget(free, row, 4)
            self.day_rows[weekday] = (is_open, opens, closes, free)
        grid.setColumnStretch(5, 1)
        card.body().addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(Button(tr("common.save"), "check", variant="primary", on_click=self.save_hours))
        card.body().addLayout(buttons)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def save_hours(self) -> bool:
        hours: dict[int, DayHours | None] = {}
        free: set[int] = set()
        try:
            for weekday, (is_open, opens, closes, is_free) in self.day_rows.items():
                hours[weekday] = (
                    DayHours(_pytime(opens.time()), _pytime(closes.time())) if is_open.isChecked() else None
                )
                if is_free.isChecked():
                    free.add(weekday)
        except TariffError:
            show_toast(self, tr("tariffs.hours_invalid"), "error")
            return False
        with self.ctx.uow(reason=tr("tariffs.tab_hours")) as session:
            ts.save_calendar(session, hours, free)
        show_toast(self, tr("common.saved"))
        return True

    # ================================================================ holidays tab
    def _holidays_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)
        add_card = Card(tr("tariffs.add_holiday"))
        row = QHBoxLayout()
        self.holiday_date = JalaliDateEdit()
        self.holiday_title = TextField(tr("tariffs.holiday_title"))
        row.addWidget(self.holiday_date, 2)
        row.addWidget(self.holiday_title, 3)
        row.addWidget(Button(tr("tariffs.add"), "plus", variant="primary", on_click=self.add_holiday))
        add_card.body().addLayout(row)
        layout.addWidget(add_card)
        list_card = Card(tr("tariffs.holidays"))
        columns = [
            Column(tr("tariffs.col_date"), lambda h: fa_date(h.day), width=140),
            Column(tr("tariffs.day"), lambda h: weekday_name(h.day.weekday()), width=120),
            Column(tr("tariffs.holiday_title"), lambda h: h.title),
        ]
        self.holiday_model = LazyTableModel(columns, self._fetch_holidays)
        self.holiday_table = DataTable(self.holiday_model)
        list_card.add(self.holiday_table, 1)
        self.holiday_empty = EmptyState("calendar", tr("tariffs.no_holidays"), tr("tariffs.no_holidays_hint"))
        list_card.add(self.holiday_empty, 1)
        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        remove_row.addWidget(Button(tr("tariffs.remove_holiday"), "x", variant="ghost", on_click=self.remove_holiday))
        list_card.body().addLayout(remove_row)
        layout.addWidget(list_card, 1)
        return page

    def _fetch_holidays(self, offset: int, limit: int) -> list[Holiday]:
        with self.ctx.read() as session:
            return HolidayRepository(session).upcoming()[offset : offset + limit]

    def add_holiday(self) -> bool:
        title = self.holiday_title.value()
        if not title:
            self.holiday_title.set_invalid(True)
            return False
        with self.ctx.uow() as session:
            ts.add_holiday(session, self.holiday_date.gregorian(), title)
        self.holiday_title.clear()
        self.holiday_title.set_invalid(False)
        show_toast(self, tr("common.saved"))
        self._reload_holidays()
        return True

    def remove_holiday(self) -> bool:
        holiday = self.holiday_table.selected_object()
        if holiday is None:
            return False
        with self.ctx.uow() as session:
            stored = session.get(Holiday, holiday.id)
            assert stored is not None
            ts.remove_holiday(session, stored, tr("tariffs.remove_holiday"))
        self._reload_holidays()
        return True

    def _reload_holidays(self) -> None:
        self.holiday_model.reset()
        has_rows = self.holiday_model.loaded_count() > 0
        self.holiday_table.setVisible(has_rows)
        self.holiday_empty.setVisible(not has_rows)

    # ================================================================ calculator tab
    def _calculator_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)
        card = Card(tr("tariffs.calculator"))
        card.add(label(tr("tariffs.calculator_hint"), "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.S)
        self.calc_entry_date = JalaliDateEdit()
        self.calc_entry_time = time_edit()
        self.calc_exit_date = JalaliDateEdit()
        self.calc_exit_time = time_edit()
        self.calc_vehicle = QComboBox()
        for vehicle in VehicleType:
            self.calc_vehicle.addItem(tr(f"vehicle.{vehicle.value}"), vehicle.value)
        self.calc_kind = QComboBox()
        for kind in VisitKind:
            self.calc_kind.addItem(tr(f"visit.{kind.value}"), kind.value)
        self.calc_coupon = QCheckBox(tr("tariffs.with_coupon"))
        self.calc_exempt = QCheckBox(tr("tariffs.night_exempt"))
        grid.addWidget(label(tr("tariffs.entry"), "caption"), 0, 0)
        grid.addWidget(self.calc_entry_date, 1, 0)
        grid.addWidget(self.calc_entry_time, 1, 1)
        grid.addWidget(label(tr("tariffs.exit"), "caption"), 2, 0)
        grid.addWidget(self.calc_exit_date, 3, 0)
        grid.addWidget(self.calc_exit_time, 3, 1)
        grid.addWidget(label(tr("tariffs.vehicle"), "caption"), 4, 0)
        grid.addWidget(self.calc_vehicle, 5, 0)
        grid.addWidget(label(tr("tariffs.kind"), "caption"), 4, 1)
        grid.addWidget(self.calc_kind, 5, 1)
        grid.addWidget(self.calc_coupon, 6, 0)
        grid.addWidget(self.calc_exempt, 6, 1)
        card.body().addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(Button(tr("tariffs.calculate"), "calculator", variant="primary", on_click=self.calculate))
        card.body().addLayout(buttons)
        layout.addWidget(card)
        self.result_card = Card(tr("tariffs.result"), raised=True)
        self.result_lines = QVBoxLayout()
        self.result_lines.setSpacing(Space.XS)
        self.result_card.body().addLayout(self.result_lines)
        self.result_total = label("", "kpi")
        self.result_card.add(self.result_total)
        self.result_card.setVisible(False)
        layout.addWidget(self.result_card)
        layout.addStretch(1)
        return page

    def calculate(self) -> PriceBreakdown | None:
        entry = local_to_utc(datetime.combine(self.calc_entry_date.gregorian(), _pytime(self.calc_entry_time.time())))
        exit_ = local_to_utc(datetime.combine(self.calc_exit_date.gregorian(), _pytime(self.calc_exit_time.time())))
        try:
            with self.ctx.read() as session:
                result = ts.load_context(session).quote(
                    entry,
                    exit_,
                    VehicleType(self.calc_vehicle.currentData()),
                    VisitKind(self.calc_kind.currentData()),
                    coupon=self.calc_coupon.isChecked(),
                    night_exempt=self.calc_exempt.isChecked(),
                )
        except TariffError:
            show_toast(self, tr("tariffs.exit_before_entry"), "error")
            return None
        self._show_result(result)
        return result

    def _show_result(self, result: PriceBreakdown) -> None:
        while self.result_lines.count():
            item = self.result_lines.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        summary = tr(
            "tariffs.summary",
            total=fa_duration(result.total_minutes),
            chargeable=fa_duration(result.chargeable_minutes),
        )
        self.result_lines.addWidget(label(summary, "muted", wrap=True))
        for line in result.lines():
            text = tr(f"price.{line.key}")
            if line.key == "extra_minutes":
                text = tr("price.extra_minutes_n", n=fa_digits(line.quantity))
            elif line.key == "night_fines":
                text = tr("price.night_fines_n", n=fa_digits(line.quantity))
            row = QHBoxLayout()
            row.addWidget(label(text), 1)
            row.addWidget(label(fa_money(line.amount)))
            holder = QWidget()
            holder.setLayout(row)
            self.result_lines.addWidget(holder)
        for flag in sorted(result.flags):
            self.result_lines.addWidget(label("• " + tr(f"flag.{flag}"), "accent"))
        self.result_total.setText(tr("price.total", amount=fa_money(result.total)))
        self.result_card.setVisible(True)

    # ================================================================ load
    def reload(self) -> None:
        now = self.ctx.clock.now_utc()
        values = self.current_values()
        for name, field in self.money.items():
            field.set_value(getattr(values, name))
        for name, spin in self.minutes.items():
            spin.setValue(getattr(values, name))
        self.moto_night.setChecked(values.motorcycle_night_fine)
        with self.ctx.read() as session:
            basis = ts.load_price_basis(session)
            calendar = ts.load_calendar(session)
        self.basis_buttons[basis].setChecked(True)
        local_now = to_local(now)
        self.effective_date.set_gregorian(local_now.date())
        self.effective_time.setTime(QTime(local_now.hour, local_now.minute).addSecs(60))
        self.note.clear()
        self.history_model.reset()
        for weekday, (is_open, opens, closes, free) in self.day_rows.items():
            hours = calendar.hours.get(weekday)
            is_open.setChecked(hours is not None)
            opens.setEnabled(hours is not None)
            closes.setEnabled(hours is not None)
            opens.setTime(_qtime(hours.open) if hours else QTime(9, 30))
            closes.setTime(_qtime(hours.close) if hours else QTime(20, 30))
            free.setChecked(weekday in calendar.free_weekdays)
        self._reload_holidays()
        self.calc_entry_date.set_gregorian(local_now.date())
        self.calc_exit_date.set_gregorian(local_now.date())
        self.calc_entry_time.setTime(QTime(10, 0))
        self.calc_exit_time.setTime(QTime(11, 13))
