"""Reports screen (SPEC §4.12): choose a report and a range, run it off the UI thread, export or print."""

from __future__ import annotations

import functools
import os
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.jalali import JalaliDate, jalali_month_length, local_date
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import Gate
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits
from caspian_parking.services.context import AppContext
from caspian_parking.services.pdf import table_html
from caspian_parking.services.reports import ReportDef, ReportError, available_reports, export_report, run_report
from caspian_parking.services.reports.base import ReportParams, ReportResult, range_params
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, label
from caspian_parking.ui.widgets.feedback import EmptyState, show_toast
from caspian_parking.ui.widgets.heatmap import HeatmapWidget
from caspian_parking.ui.widgets.inputs import JalaliDateEdit, TimeField
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel
from caspian_parking.ui.workers import run_in_background

CHAR_PX = 9  # report widths are given in characters (like Excel)
PRESETS = ("today", "yesterday", "week", "month", "last_month", "custom")


def preset_range(preset: str, today: date) -> tuple[date, date]:
    j = JalaliDate.from_gregorian(today)
    if preset == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if preset == "week":
        return today - timedelta(days=6), today
    if preset == "month":
        return JalaliDate(j.year, j.month, 1).to_gregorian(), today
    if preset == "last_month":
        year, month = (j.year - 1, 12) if j.month == 1 else (j.year, j.month - 1)
        first = JalaliDate(year, month, 1).to_gregorian()
        return first, JalaliDate(year, month, jalali_month_length(year, month)).to_gregorian()
    return today, today


class ReportsScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("reports.title"), tr("reports.subtitle"))
        self.result: ReportResult | None = None
        self.busy = False
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)

        list_card = Card(tr("reports.list"))
        self.report_list = QListWidget()
        self.reports: list[ReportDef] = available_reports(ctx)
        for report in self.reports:
            item = QListWidgetItem(f"{fa_digits(report.number)}. {report.title}")
            item.setData(Qt.ItemDataRole.UserRole, report.key)
            self.report_list.addItem(item)
        self.report_list.currentRowChanged.connect(lambda _r: self._report_changed())
        list_card.add(self.report_list, 1)
        if ctx.can(Permission.CHANGE_SETTINGS):
            list_card.add(self._daily_card())
        row.addWidget(list_card, 2)

        right = QVBoxLayout()
        right.setSpacing(Space.M)
        params = Card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.M)
        self.preset = QComboBox()
        for preset in PRESETS:
            self.preset.addItem(tr(f"reports.preset_{preset}"), preset)
        self.preset.currentIndexChanged.connect(lambda _i: self._apply_preset())
        self.start_date = JalaliDateEdit()
        self.end_date = JalaliDateEdit()
        self.gate = QComboBox()
        self.gate.addItem(tr("reports.all_gates"), None)
        self.operator = QComboBox()
        self.operator.addItem(tr("reports.all_operators"), None)
        with ctx.read() as session:
            from sqlalchemy import select

            for gate in session.scalars(select(Gate).order_by(Gate.code)):
                self.gate.addItem(gate.name, gate.code)
            for user in UserRepository(session).page(0, 500):
                self.operator.addItem(user.display_name, user.id)
        self.text = TextField(tr("reports.plate"))
        for column, (key, widget) in enumerate(
            (("reports.range_label", self.preset), ("reports.from", self.start_date), ("reports.to", self.end_date))
        ):
            grid.addWidget(label(tr(key), "caption"), 0, column)
            grid.addWidget(widget, 1, column)
        grid.addWidget(label(tr("reports.gate"), "caption"), 2, 0)
        grid.addWidget(self.gate, 3, 0)
        grid.addWidget(label(tr("reports.operator"), "caption"), 2, 1)
        grid.addWidget(self.operator, 3, 1)
        grid.addWidget(self.text, 3, 2)
        params.body().addLayout(grid)
        actions = QHBoxLayout()
        self.run_button = Button(tr("reports.run"), "chart-column", variant="primary", on_click=self.run)
        actions.addWidget(self.run_button)
        actions.addStretch(1)
        self.export_buttons = []
        for kind, icon_name in (("excel", "file-spreadsheet"), ("word", "file-text"), ("pdf", "download")):
            button = Button(tr(f"reports.export_{kind}"), icon_name, on_click=functools.partial(self.export, kind))
            self.export_buttons.append(button)
            actions.addWidget(button)
        self.print_button = Button(tr("reports.print"), "printer", on_click=self.print_report)
        actions.addWidget(self.print_button)
        params.body().addLayout(actions)
        right.addWidget(params)

        self.result_card = Card()
        self.local_banner = chip(tr("reports.local_banner"), "warning")
        self.local_banner.setVisible(False)
        self.result_card.add(self.local_banner)
        self.kpis = QHBoxLayout()
        self.kpis.setSpacing(Space.XS)
        self.result_card.body().addLayout(self.kpis)
        self.heatmap = HeatmapWidget()
        self.heatmap.setVisible(False)
        self.result_card.add(self.heatmap)
        self._rows: list[list[object]] = []
        self.model = LazyTableModel([], lambda o, lim: self._rows[o : o + lim])
        self.table = DataTable(self.model)
        self.result_card.add(self.table, 1)
        self.empty = EmptyState("chart-column", tr("reports.empty"), tr("reports.empty_hint"))
        self.result_card.add(self.empty, 1)
        right.addWidget(self.result_card, 1)
        row.addLayout(right, 5)

        can_export = ctx.can(Permission.EXPORT_REPORTS)
        for button in [*self.export_buttons, self.print_button]:
            button.setVisible(can_export)
        self._set_result(None)
        if self.reports:
            self.report_list.setCurrentRow(0)
        self._apply_preset()

    # ---------------------------------------------------------------- parameters
    def _today(self) -> date:
        return local_date(self.ctx.clock.now_utc())

    def _apply_preset(self) -> None:
        preset = self.preset.currentData()
        custom = preset == "custom"
        self.start_date.setEnabled(custom)
        self.end_date.setEnabled(custom)
        if not custom:
            first, last = preset_range(preset, self._today())
            self.start_date.set_gregorian(first)
            self.end_date.set_gregorian(last)

    def current_report(self) -> ReportDef | None:
        row = self.report_list.currentRow()
        return self.reports[row] if 0 <= row < len(self.reports) else None

    def select_report(self, key: str) -> None:
        for index, report in enumerate(self.reports):
            if report.key == key:
                self.report_list.setCurrentRow(index)

    def _report_changed(self) -> None:
        report = self.current_report()
        self.text.setVisible(bool(report and report.needs_text))
        if report is not None and report.needs_text:
            self.text.setPlaceholderText(
                tr("reports.shop_name" if report.key == "shop_performance" else "reports.plate")
            )

    def params(self) -> ReportParams:
        first, last = self.start_date.gregorian(), self.end_date.gregorian()
        if last < first:
            first, last = last, first
        return range_params(
            first,
            last,
            gate_code=self.gate.currentData(),
            operator_id=self.operator.currentData(),
            text=self.text.value(),
        )

    # ---------------------------------------------------------------- run
    def run(self) -> None:
        report = self.current_report()
        if report is None or self.busy:
            return
        self.busy = True
        self.run_button.setEnabled(False)
        params = self.params()
        run_in_background(lambda: run_report(self.ctx, report.key, params), self._done, self._failed)

    def _done(self, result: ReportResult) -> None:
        self.busy = False
        self.run_button.setEnabled(True)
        self._set_result(result)

    def _failed(self, error: Exception) -> None:
        self.busy = False
        self.run_button.setEnabled(True)
        key = str(error) if isinstance(error, ReportError) else "reports.failed"
        show_toast(self, tr(key), "error")

    def _set_result(self, result: ReportResult | None) -> None:
        self.result = result
        while self.kpis.count():
            item = self.kpis.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        has = result is not None and (result.row_count() > 0 or bool(result.kpis))
        self.table.setVisible(has)
        self.empty.setVisible(not has)
        for button in [*self.export_buttons, self.print_button]:
            button.setEnabled(result is not None)
        self.local_banner.setVisible(result is not None and result.local_only)
        if result is None:
            self.heatmap.setVisible(False)
            return
        for name, value in result.kpis:
            self.kpis.addWidget(chip(f"{name}: {value}", "accent"))
        self.kpis.addStretch(1)
        rows: list[list[object]] = []
        for section in result.sections:
            if section.title:
                header: list[object] = ["▸ " + section.title, *[""] * (len(result.columns) - 1)]
                rows.append(header)
            rows.extend(section.rows)
        self._rows = rows
        widths = result.widths or [18] * len(result.columns)
        columns = [
            Column(title, functools.partial(_cell, index=i), width=CHAR_PX * (widths[i] if i < len(widths) else 18))
            for i, title in enumerate(result.columns)
        ]
        self.model = LazyTableModel(columns, lambda o, lim: self._rows[o : o + lim])
        self.table.setModel(self.model)
        for index, column in enumerate(columns):
            if column.width:
                self.table.setColumnWidth(index, column.width)
        self.model.reset()
        if result.chart:
            self.heatmap.set_data(result.chart["matrix"], result.chart["rows"], result.chart.get("max"))
        self.heatmap.setVisible(bool(result.chart))

    # ---------------------------------------------------------------- export / print
    def export(self, kind: str, open_file: bool = True) -> Path | None:
        if self.result is None:
            return None
        path = export_report(self.result, self.ctx.data_root.exports, kind, stamp_source=self.ctx.clock.now_utc())
        show_toast(self, tr("subs.exported", path=path.name))
        if open_file and hasattr(os, "startfile"):  # pragma: no cover
            os.startfile(path)  # type: ignore[attr-defined]
        return path

    def print_report(self) -> None:  # pragma: no cover - opens the system print dialog
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        if self.result is None:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec():
            document = QTextDocument()
            document.setHtml(table_html(self.result.to_table()))
            document.print_(printer)

    # ---------------------------------------------------------------- daily report settings
    def _daily_card(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, Space.M, 0, 0)
        layout.addWidget(label(tr("reports.daily_title"), "title"))
        with self.ctx.read() as session:
            enabled = bool(get_setting(session, "reports.daily_enabled"))
            at = str(get_setting(session, "reports.daily_time"))
            folder = str(get_setting(session, "reports.daily_folder") or "")
        from PySide6.QtCore import QTime
        from PySide6.QtWidgets import QCheckBox

        self.daily_enabled = QCheckBox(tr("reports.daily_enabled"))
        self.daily_enabled.setChecked(enabled)
        self.daily_time = TimeField(QTime.fromString(at, "HH:mm"))
        self.daily_folder = TextField(tr("reports.daily_folder"), persian_digits=False)
        self.daily_folder.setText(folder)
        layout.addWidget(self.daily_enabled)
        row = QHBoxLayout()
        row.addWidget(label(tr("reports.daily_time"), "caption"))
        row.addWidget(self.daily_time)
        layout.addLayout(row)
        layout.addWidget(self.daily_folder)
        layout.addWidget(Button(tr("common.save"), "check", on_click=self.save_daily))
        return box

    def save_daily(self) -> None:
        with self.ctx.uow(reason=tr("reports.daily_title")) as session:
            set_setting(session, "reports.daily_enabled", self.daily_enabled.isChecked())
            set_setting(session, "reports.daily_time", self.daily_time.time().toString("HH:mm"))
            set_setting(session, "reports.daily_folder", self.daily_folder.text().strip())
        show_toast(self, tr("common.saved"))


def _cell(row: list[object], index: int) -> str:
    return _display(row[index]) if index < len(row) else ""


def _display(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return fa_digits(f"{value:,}".replace(",", "٬"))
    return "" if value is None else str(value)
