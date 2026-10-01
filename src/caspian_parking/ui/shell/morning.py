"""Morning report (SPEC §4.10): the first user of the day sees the night's after-hours passes and
acknowledges them; who and when are stored (``morning_acks``)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QWidget

from caspian_parking.data.models import MorningAck
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date, fa_digits, fa_time
from caspian_parking.services.context import AppContext
from caspian_parking.services.receipts import plate_of
from caspian_parking.services.watch import MorningReport, NightPass, WatchService
from caspian_parking.ui.widgets.alerts import LightDelegate
from caspian_parking.ui.widgets.basics import label
from caspian_parking.ui.widgets.feedback import ModalDialog
from caspian_parking.ui.widgets.plate_input import PlateDelegate
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel


class MorningReportDialog(ModalDialog):
    def __init__(self, parent: QWidget | None, report: MorningReport) -> None:
        super().__init__(parent, tr("watch.morning_title", day=fa_date(report.day)), width=720)
        summary = tr("watch.morning_summary", n=fa_digits(len(report.passes)), blocked=fa_digits(report.blocked_count))
        self.content.addWidget(label(summary, "title", wrap=True))
        self.passes = report.passes
        model = LazyTableModel(
            [
                Column("", lambda _p: "", width=36),
                Column(tr("gate.col_plate"), lambda _p: "", width=170),
                Column(tr("gate.col_time"), lambda p: fa_time(p.read.created_at_utc), width=80),
                Column(tr("camera.col_lane"), lambda p: tr(f"camera.lane_{p.read.lane}"), width=70),
                Column(tr("watch.col_note"), lambda p: tr("watch.blocked") if p.blocked else ""),
            ],
            lambda o, lim: self.passes[o : o + lim],
        )
        self.table = DataTable(model)
        self.table.setItemDelegateForColumn(
            0, LightDelegate(lambda p: ("black" if p.blocked else None) if p else None, self.table)
        )
        self.table.setItemDelegateForColumn(1, PlateDelegate(lambda p: plate_of(p.read) if p else None, self.table))
        self.content.addWidget(self.table, 1)
        self.add_button(tr("watch.acknowledge"), variant="primary")


def show_morning_report(parent: QWidget | None, ctx: AppContext, ask: bool = True) -> MorningAck | None:
    """At login: show the night report once per day; returns the acknowledgement written."""
    service = WatchService(ctx)
    report = service.pending_report()
    if report is None:
        return None
    if ask and MorningReportDialog(parent, report).exec() != QDialog.DialogCode.Accepted:  # pragma: no cover
        return None
    return service.acknowledge(report)


__all__ = ["MorningReportDialog", "NightPass", "show_morning_report"]
