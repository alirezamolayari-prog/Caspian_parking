"""Dialogs used by the gate screen: reason, receipt preview, already-inside, lost ticket, night list."""

from __future__ import annotations

from datetime import datetime, time

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QWidget

from caspian_parking.core.jalali import JalaliDate, local_to_utc, to_local
from caspian_parking.data.models import ActiveSession
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_ltr, fa_time
from caspian_parking.services.gate_service import GateService
from caspian_parking.services.receipts import plate_of
from caspian_parking.ui.widgets.basics import TextField, label
from caspian_parking.ui.widgets.feedback import ModalDialog
from caspian_parking.ui.widgets.inputs import JalaliDateEdit, TimeField
from caspian_parking.ui.widgets.plate import PlateWidget


class ReasonDialog(ModalDialog):
    """Asks for a mandatory reason (cancellations, manual amounts, waivers)."""

    def __init__(self, parent: QWidget | None, title: str, hint: str = "") -> None:
        super().__init__(parent, title, width=480)
        if hint:
            self.content.addWidget(label(hint, "muted", wrap=True))
        self.reason = TextField(tr("gate.reason_placeholder"))
        self.content.addWidget(self.reason)
        self.add_button(tr("common.cancel"), role="reject")
        self.ok = self.add_button(tr("common.ok"), variant="primary", role="none")
        self.ok.clicked.connect(self._accept_if_filled)
        self.reason.returnPressed.connect(self._accept_if_filled)

    def _accept_if_filled(self) -> None:
        if self.reason.value():
            self.accept()
        else:
            self.reason.set_invalid(True)

    def value(self) -> str:
        return self.reason.value()


def ask_reason(parent: QWidget, title: str, hint: str = "") -> str | None:
    dialog = ReasonDialog(parent, title, hint)
    return dialog.value() if dialog.exec() == QDialog.DialogCode.Accepted else None


class ReceiptPreviewDialog(ModalDialog):
    """What you see is what prints (same 1-bit image)."""

    def __init__(self, parent: QWidget | None, pixmap: QPixmap, title: str | None = None) -> None:
        super().__init__(parent, title or tr("receipt.preview"), width=420)
        area = QScrollArea()
        area.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setPixmap(pixmap.scaledToWidth(360, Qt.TransformationMode.SmoothTransformation))
        area.setWidget(image_label)
        area.setMinimumHeight(520)
        self.content.addWidget(area)
        self.add_button(tr("common.close"), role="reject")
        self.print_button = self.add_button(tr("receipt.print"), variant="primary", icon_name="printer")


class AlreadyInsideDialog(ModalDialog):
    EXIT = 10
    DUPLICATE = 11

    def __init__(self, parent: QWidget | None, session: ActiveSession) -> None:
        super().__init__(parent, tr("gate.already_inside_title"), width=520)
        row = QHBoxLayout()
        row.addWidget(PlateWidget(plate_of(session), height=56))
        row.addStretch(1)
        self.content.addLayout(row)
        self.content.addWidget(
            label(
                tr(
                    "gate.already_inside_body", time=fa_datetime(session.entry_at_utc), ticket=fa_ltr(session.ticket_no)
                ),
                wrap=True,
            )
        )
        self.add_button(tr("common.cancel"), role="reject")
        duplicate = self.add_button(tr("gate.print_duplicate"), role="none", icon_name="printer")
        duplicate.clicked.connect(lambda: self.done(self.DUPLICATE))
        exit_button = self.add_button(tr("gate.go_to_exit"), variant="primary", role="none", icon_name="log-out")
        exit_button.clicked.connect(lambda: self.done(self.EXIT))


class LostTicketDialog(ModalDialog):
    """Find the vehicle by plate or today's entries, then reprint (المثنی) or collect without printing."""

    DUPLICATE = 10
    COLLECT = 11

    def __init__(self, parent: QWidget | None, gate: GateService) -> None:
        super().__init__(parent, tr("gate.lost_title"), width=620)
        self.gate = gate
        self.search = TextField(tr("gate.lost_search"))
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.content.addWidget(self.search)
        self.results = QListWidget()
        self.results.setMinimumHeight(320)
        self.content.addWidget(self.results)
        self.add_button(tr("common.cancel"), role="reject")
        duplicate = self.add_button(tr("gate.print_duplicate"), role="none", icon_name="printer")
        duplicate.clicked.connect(lambda: self._finish(self.DUPLICATE))
        collect = self.add_button(
            tr("gate.collect_without_print"), variant="primary", role="none", icon_name="calculator"
        )
        collect.clicked.connect(lambda: self._finish(self.COLLECT))
        self.refresh()

    def refresh(self) -> None:
        self.results.clear()
        text = self.search.value()
        sessions = self.gate.search_inside(text, limit=200)
        if not text:
            today = self.gate.today_start()
            sessions = [s for s in sessions if s.entry_at_utc >= today] or sessions
        for session in sessions:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, session)
            self.results.addItem(item)
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(4, 2, 4, 2)
            row.addWidget(PlateWidget(plate_of(session), height=34))
            vehicle = tr(f"vehicle.{session.vehicle_type}")
            details = f"{fa_time(session.entry_at_utc)}   {fa_ltr(session.ticket_no)}   {vehicle}"
            row.addWidget(label(details, "muted"))
            row.addStretch(1)
            item.setSizeHint(widget.sizeHint())
            self.results.setItemWidget(item, widget)
        if self.results.count():
            self.results.setCurrentRow(0)

    def selected(self) -> ActiveSession | None:
        item = self.results.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _finish(self, code: int) -> None:
        if self.selected() is not None:
            self.done(code)


class ForeignTicketDialog(ModalDialog):
    """Another gate's ticket that is not here yet (link down): exit with the entry time on the ticket."""

    def __init__(self, parent: QWidget | None, ticket_no: str, entry_at: datetime | None) -> None:
        super().__init__(parent, tr("gate.foreign_title"), width=520)
        self.content.addWidget(label(tr("gate.foreign_body", ticket=fa_ltr(ticket_no)), wrap=True))
        local = to_local(entry_at) if entry_at is not None else None
        self.date = JalaliDateEdit(JalaliDate.from_gregorian(local.date()) if local else None)
        self.time = TimeField(QTime(local.hour, local.minute) if local else None)
        row = QHBoxLayout()
        row.addWidget(label(tr("gate.foreign_entry_time"), "caption"))
        row.addWidget(self.date, 2)
        row.addWidget(self.time, 1)
        self.content.addLayout(row)
        if entry_at is not None:  # time read from the signed barcode: shown, not editable
            self.date.setEnabled(False)
            self.time.setEnabled(False)
        self.add_button(tr("common.cancel"), role="reject")
        self.add_button(tr("gate.foreign_continue"), variant="primary")

    def entry_at(self) -> datetime:
        moment = self.time.time()
        return local_to_utc(datetime.combine(self.date.gregorian(), time(moment.hour(), moment.minute())))
