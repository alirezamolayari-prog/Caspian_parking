"""Dashboard: today's KPIs, occupancy per level, 30-day heatmap, latest entries."""

from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QProgressBar
from sqlalchemy import func, select

from caspian_parking.core.jalali import local_date
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import Debt, EntryEvent
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_money, fa_number, fa_time
from caspian_parking.services.context import AppContext
from caspian_parking.services.follow_up import follow_up_rows
from caspian_parking.services.gate_service import GateService, open_debts_total
from caspian_parking.services.receipts import plate_of
from caspian_parking.services.reports import run_report
from caspian_parking.services.reports.base import day_params, range_params
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, label
from caspian_parking.ui.widgets.heatmap import HeatmapWidget
from caspian_parking.ui.widgets.plate_input import PlateDelegate
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel
from caspian_parking.ui.workers import run_in_background

REFRESH_MS = 60_000
KPI_KEYS = ("inside", "entries", "revenue", "follow_up", "debts")


class DashboardScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("dash.title"), tr("dash.subtitle"))
        self.gate = GateService(ctx)
        self.header_actions.addWidget(Button(tr("dash.refresh"), "refresh-cw", on_click=self.refresh))
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(Space.L)
        self.kpi_values: dict[str, object] = {}
        for key in KPI_KEYS:
            if key == "revenue" and not ctx.can(Permission.VIEW_FINANCIAL_REPORTS):
                continue
            card = Card(raised=True)
            card.add(label(tr(f"dash.kpi_{key}"), "muted"))
            value = label("—", "kpi")
            card.add(value)
            self.kpi_values[key] = value
            kpi_row.addWidget(card)
        self.body.addLayout(kpi_row)
        middle = QHBoxLayout()
        middle.setSpacing(Space.L)
        occupancy = Card(tr("dash.occupancy"))
        self.levels = QGridLayout()
        occupancy.body().addLayout(self.levels)
        occupancy.body().addStretch(1)
        middle.addWidget(occupancy, 2)
        heat = Card(tr("dash.heatmap"))
        self.heatmap = HeatmapWidget()
        heat.add(self.heatmap)
        middle.addWidget(heat, 5)
        self.body.addLayout(middle)
        recent = Card(tr("dash.recent"))
        self.recent_model = LazyTableModel(
            [
                Column(tr("gate.col_plate"), lambda _e: "", width=160),
                Column(tr("gate.col_time"), lambda e: fa_time(e.entry_at_utc), width=80),
                Column(tr("gate.col_type"), lambda e: tr(f"vehicle.{e.vehicle_type}"), width=100),
                Column(tr("dash.category"), lambda e: tr(f"category.{e.category}")),
            ],
            self.gate.todays_entries,
        )
        self.recent = DataTable(self.recent_model)
        self.recent.setItemDelegateForColumn(0, PlateDelegate(plate_of, self.recent))
        recent.add(self.recent, 1)
        self.body.addWidget(recent, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.heatmap_loaded = False
        self.refresh()

    def kpis(self) -> dict[str, int]:
        now = self.ctx.clock.now_utc()
        today = local_date(now)
        with self.ctx.read() as session:
            start = day_params(today).start
            entries = int(
                session.scalar(select(func.count()).select_from(EntryEvent).where(EntryEvent.entry_at_utc >= start))
                or 0
            )
            plates = session.scalars(select(Debt.plate_key).distinct()).all()
            debts = sum(open_debts_total(session, p) for p in plates if p)
        values = {
            "inside": self.gate.inside_count(),
            "entries": entries,
            "follow_up": len(follow_up_rows(self.ctx)),
            "debts": debts,
        }
        if "revenue" in self.kpi_values:
            values["revenue"] = run_report(self.ctx, "financial", day_params(today), check_permission=False).totals[
                "grand"
            ]
        return values

    def refresh(self) -> None:
        values = self.kpis()
        for key, widget in self.kpi_values.items():
            value = values.get(key, 0)
            text = fa_money(value) if key in ("revenue", "debts") else fa_number(value)
            widget.setText(text)  # type: ignore[attr-defined]
        while self.levels.count():
            item = self.levels.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for row, level in enumerate(self.gate.occupancy()):
            self.levels.addWidget(label(level.name, "title"), row * 2, 0)
            inside_text = tr("dash.level_inside", inside=fa_number(level.inside), capacity=fa_number(level.capacity))
            self.levels.addWidget(label(inside_text, "muted"), row * 2, 1)
            bar = QProgressBar()
            bar.setRange(0, max(1, level.capacity))
            bar.setValue(min(level.inside, level.capacity))
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            self.levels.addWidget(bar, row * 2 + 1, 0, 1, 2)
        self.recent_model.reset()
        self.load_heatmap()

    def load_heatmap(self) -> None:
        today = local_date(self.ctx.clock.now_utc())
        params = range_params(today - timedelta(days=29), today)
        run_in_background(
            lambda: run_report(self.ctx, "occupancy", params, check_permission=False),
            self._heatmap_done,
        )

    def _heatmap_done(self, result: object) -> None:
        chart = getattr(result, "chart", None)
        if chart:
            self.heatmap.set_data(chart["matrix"], chart["rows"], chart.get("max"))
        self.heatmap_loaded = True

    def on_show(self) -> None:
        self.refresh()
