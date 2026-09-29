"""System (SPEC §4.13, §2.5, §4.10): backup & restore, fiscal year, photos & disk, maintenance,
power outages and training mode."""

from __future__ import annotations

import functools
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QSpinBox, QTabWidget, QVBoxLayout, QWidget

from caspian_parking.core.jalali import local_date
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import FiscalYear
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date, fa_datetime, fa_digits, fa_number
from caspian_parking.services import backup, fiscal, maintenance, photos
from caspian_parking.services.context import AppContext
from caspian_parking.services.reports import run_report
from caspian_parking.services.reports.base import range_params
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import ask_reason
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import confirm, show_toast
from caspian_parking.ui.widgets.inputs import TimeField
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

BYTES_PER_GB = 1024**3


def _cell(row: list[object], index: int) -> str:
    return str(row[index]) if index < len(row) else ""


def _page() -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
    layout.setSpacing(Space.L)
    return page, layout


class SystemScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("system.title"), tr("system.subtitle"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._backup_tab(), tr("system.tab_backup"))
        if ctx.can(Permission.CLOSE_FISCAL_YEAR):
            self.tabs.addTab(self._fiscal_tab(), tr("system.tab_fiscal"))
        self.tabs.addTab(self._storage_tab(), tr("system.tab_storage"))
        self.tabs.addTab(self._outages_tab(), tr("system.tab_outages"))
        self.tabs.addTab(self._training_tab(), tr("system.tab_training"))
        self.body.addWidget(self.tabs, 1)
        self.reload()

    # ================================================================ backup
    def _backup_tab(self) -> QWidget:
        page, layout = _page()
        status = Card(tr("backup.status"))
        self.backup_status = label("", "title")
        status.add(self.backup_status)
        row = QHBoxLayout()
        row.addWidget(Button(tr("backup.now"), "hard-drive-download", variant="primary", on_click=self.backup_now))
        row.addWidget(Button(tr("backup.test"), "list-checks", on_click=self.test_selected))
        row.addWidget(
            Button(tr("backup.restore"), "hard-drive-upload", variant="danger", on_click=self.restore_selected)
        )
        row.addStretch(1)
        status.body().addLayout(row)
        self.backup_model = LazyTableModel(
            [
                Column(tr("backup.col_file"), lambda b: b.path.name, width=280),
                Column(tr("backup.col_size"), lambda b: fa_digits(f"{b.size / 1024 / 1024:.1f}") + " MB", width=100),
                Column(tr("backup.col_folder"), lambda b: str(b.path.parent)),
            ],
            lambda o, lim: backup.list_backups(self.ctx.data_root.backups)[o : o + lim],
        )
        self.backup_table = DataTable(self.backup_model)
        status.add(self.backup_table, 1)
        layout.addWidget(status, 1)
        settings = Card(tr("backup.settings"))
        with self.ctx.read() as session:
            enabled = bool(get_setting(session, "backup.enabled"))
            at = str(get_setting(session, "backup.time"))
            keep = int(get_setting(session, "backup.keep"))
            on_close = bool(get_setting(session, "backup.on_close"))
        self.backup_enabled = QCheckBox(tr("backup.enabled"))
        self.backup_enabled.setChecked(enabled)
        self.backup_on_close = QCheckBox(tr("backup.on_close"))
        self.backup_on_close.setChecked(on_close)
        self.backup_time = TimeField(QTime.fromString(at, "HH:mm"))
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 365)
        self.backup_keep.setValue(keep)
        self.backup_extra = TextField(tr("backup.extra_folders"), persian_digits=False)
        self.backup_extra.setText("; ".join(self.ctx.config.backup_destinations))
        line = QHBoxLayout()
        for widget in (self.backup_enabled, self.backup_on_close):
            line.addWidget(widget)
        line.addWidget(label(tr("backup.time"), "caption"))
        line.addWidget(self.backup_time)
        line.addWidget(label(tr("backup.keep"), "caption"))
        line.addWidget(self.backup_keep)
        line.addStretch(1)
        settings.body().addLayout(line)
        settings.add(self.backup_extra)
        settings.add(Button(tr("common.save"), "check", on_click=self.save_backup_settings))
        layout.addWidget(settings)
        return page

    def backup_now(self) -> list[backup.BackupInfo]:
        try:
            results = backup.create_backup(self.ctx)
        except backup.BackupError as exc:
            show_toast(self, tr(str(exc)), "error")
            return []
        show_toast(self, tr("backup.done", n=fa_digits(len(results))))
        self.reload()
        return results

    def selected_backup(self) -> Path | None:
        info = self.backup_table.selected_object()
        return info.path if info is not None else None

    def test_selected(self) -> backup.RestoreCheck | None:
        path = self.selected_backup()
        if path is None:
            show_toast(self, tr("backup.select"), "info")
            return None
        try:
            result = backup.test_restore(path)
        except backup.BackupError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        key = "backup.test_ok" if result.ok else "backup.test_bad"
        show_toast(
            self,
            tr(key, tables=fa_digits(result.tables), visits=fa_number(result.visits)),
            "success" if result.ok else "error",
        )
        return result

    def restore_selected(self, ask: bool = True) -> bool:
        path = self.selected_backup()
        if path is None:
            show_toast(self, tr("backup.select"), "info")
            return False
        if ask and not confirm(self, tr("backup.restore"), tr("backup.restore_confirm", name=path.name), danger=True):
            return False
        try:
            backup.restore_backup(self.ctx, path)
        except backup.BackupError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        if ask:  # pragma: no cover - quits the application
            confirm(self, tr("backup.restore"), tr("backup.restart"))
            QApplication.quit()
        return True

    def save_backup_settings(self) -> None:
        with self.ctx.uow(reason=tr("backup.settings")) as session:
            set_setting(session, "backup.enabled", self.backup_enabled.isChecked())
            set_setting(session, "backup.on_close", self.backup_on_close.isChecked())
            set_setting(session, "backup.time", self.backup_time.time().toString("HH:mm"))
            set_setting(session, "backup.keep", self.backup_keep.value())
        self.ctx.config.backup_destinations = [p.strip() for p in self.backup_extra.text().split(";") if p.strip()]
        self.ctx.save_config()
        show_toast(self, tr("common.saved"))

    # ================================================================ fiscal year
    def _fiscal_tab(self) -> QWidget:
        page, layout = _page()
        card = Card(tr("fiscal.current"))
        self.fiscal_info = label("", "title", wrap=True)
        card.add(self.fiscal_info)
        card.add(label(tr("fiscal.close_hint"), "muted", wrap=True))
        row = QHBoxLayout()
        self.fiscal_confirm = TextField(tr("fiscal.confirm_placeholder"))
        row.addWidget(self.fiscal_confirm, 1)
        row.addWidget(Button(tr("fiscal.close"), "lock", variant="danger", on_click=self.close_year))
        card.body().addLayout(row)
        layout.addWidget(card)
        history = Card(tr("fiscal.history"))
        self.fiscal_model = LazyTableModel(
            [
                Column(tr("fiscal.col_name"), lambda y: fa_digits(y.name), width=90),
                Column(tr("fiscal.col_from"), lambda y: fa_date(y.start_date), width=110),
                Column(tr("fiscal.col_to"), lambda y: fa_date(y.end_date), width=110),
                Column(tr("fiscal.col_status"), lambda y: tr(f"fiscal.status_{y.status}"), width=90),
                Column(
                    tr("fiscal.col_total"),
                    lambda y: fa_number((y.final_counters or {}).get("total", 0)) if y.final_counters else "—",
                ),
            ],
            self._fetch_years,
        )
        history.add(DataTable(self.fiscal_model), 1)
        layout.addWidget(history, 1)
        return page

    def _fetch_years(self, offset: int, limit: int) -> list[FiscalYear]:
        with self.ctx.read() as session:
            return fiscal.years(session)[offset : offset + limit]

    def close_year(self, reason: str | None = None) -> FiscalYear | None:
        reason = reason or ask_reason(self, tr("fiscal.close"), tr("fiscal.close_hint"))
        if not reason:
            return None
        try:
            year = fiscal.close_year(self.ctx, self.fiscal_confirm.value(), reason)
        except fiscal.FiscalError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("fiscal.closed", name=fa_digits(year.name)))
        self.fiscal_confirm.clear()
        self.reload()
        return year

    # ================================================================ storage & maintenance
    def _storage_tab(self) -> QWidget:
        page, layout = _page()
        card = Card(tr("storage.photos"))
        self.storage_info = label("", "title", wrap=True)
        card.add(self.storage_info)
        row = QHBoxLayout()
        self.photo_folder = TextField(tr("storage.folder"), persian_digits=False)
        self.photo_folder.setText(self.ctx.config.photos_folder)
        self.retention = QSpinBox()
        self.retention.setRange(7, 3650)
        with self.ctx.read() as session:
            self.retention.setValue(int(get_setting(session, "photos.retention_days")))
        self.retention.setSuffix(" " + tr("tariff.unit_days"))
        row.addWidget(self.photo_folder, 2)
        row.addWidget(label(tr("storage.retention"), "caption"))
        row.addWidget(self.retention)
        card.body().addLayout(row)
        actions = QHBoxLayout()
        actions.addWidget(Button(tr("common.save"), "check", on_click=self.save_storage))
        actions.addWidget(Button(tr("storage.cleanup"), "rotate-ccw", on_click=self.cleanup))
        actions.addStretch(1)
        card.body().addLayout(actions)
        layout.addWidget(card)
        maintenance_card = Card(tr("maint.title"))
        self.maintenance_info = label("", "muted")
        maintenance_card.add(self.maintenance_info)
        maintenance_card.add(Button(tr("maint.run"), "database", on_click=self.run_maintenance))
        layout.addWidget(maintenance_card)
        layout.addStretch(1)
        return page

    def save_storage(self) -> None:
        with self.ctx.uow() as session:
            set_setting(session, "photos.retention_days", self.retention.value())
        self.ctx.config.photos_folder = self.photo_folder.text().strip()
        self.ctx.save_config()
        show_toast(self, tr("common.saved"))
        self.reload()

    def cleanup(self) -> int:
        removed = photos.cleanup(self.ctx)
        show_toast(self, tr("storage.cleaned", n=fa_digits(len(removed))))
        self.reload()
        return len(removed)

    def run_maintenance(self) -> bool:
        done = maintenance.run_maintenance(self.ctx, force=True)
        show_toast(self, tr("maint.done"))
        self.reload()
        return done

    # ================================================================ outages
    def _outages_tab(self) -> QWidget:
        page, layout = _page()
        self._outage_rows: list[list[object]] = []
        self.outage_model = LazyTableModel(
            [
                Column(tr(key), functools.partial(_cell, index=i))
                for i, key in enumerate(("outage.node", "outage.from", "outage.to", "rep.col_duration", "rep.col_kind"))
            ],
            lambda o, lim: self._outage_rows[o : o + lim],
        )
        layout.addWidget(DataTable(self.outage_model), 1)
        return page

    # ================================================================ training mode
    def _training_tab(self) -> QWidget:
        page, layout = _page()
        card = Card(tr("training.title"))
        card.add(label(tr("training.hint"), "muted", wrap=True))
        self.training_toggle = QCheckBox(tr("training.enable"))
        self.training_toggle.setChecked(self.ctx.config.training_mode)
        card.add(self.training_toggle)
        card.add(Button(tr("common.save"), "check", on_click=self.save_training))
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def save_training(self) -> None:
        self.ctx.config.training_mode = self.training_toggle.isChecked()
        self.ctx.save_config()
        show_toast(self, tr("training.restart"), "info")

    # ================================================================ refresh
    def reload(self) -> None:
        last = backup.last_success(self.ctx)
        self.backup_status.setText(tr("backup.last", when=fa_datetime(last)) if last else tr("backup.never"))
        self.backup_model.reset()
        if hasattr(self, "fiscal_model"):
            with self.ctx.read() as session:
                year = fiscal.open_year(session)
            if year is not None:
                counters = self.ctx_counters()
                self.fiscal_info.setText(
                    tr(
                        "fiscal.info",
                        name=fa_digits(year.name),
                        start=fa_date(year.start_date),
                        end=fa_date(year.end_date),
                        total=fa_number(counters.get("total", 0)),
                    )
                )
            self.fiscal_model.reset()
        stats = photos.usage(self.ctx)
        days = fa_number(stats.days_left) if stats.days_left is not None else "—"
        self.storage_info.setText(
            tr(
                "storage.info",
                mb=fa_digits(f"{stats.mb_per_day:.1f}"),
                free=fa_digits(f"{stats.free_bytes / BYTES_PER_GB:.1f}"),
                percent=fa_digits(f"{stats.free_percent:.0f}"),
                days=days,
            )
        )
        with self.ctx.read() as session:
            last_maintenance = str(get_setting(session, "maintenance.last_run") or "")
        self.maintenance_info.setText(tr("maint.last", when=last_maintenance[:16] or "—"))
        today = local_date(self.ctx.clock.now_utc())
        result = run_report(
            self.ctx, "outages", range_params(today - timedelta(days=365), today), check_permission=False
        )
        self._outage_rows = [row for section in result.sections for row in section.rows]
        self.outage_model.reset()

    def ctx_counters(self) -> dict[str, int]:
        from caspian_parking.services.gate_service import GateService

        return GateService(self.ctx).counters()

    def on_show(self) -> None:
        self.reload()
