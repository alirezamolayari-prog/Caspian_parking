"""Settings tabs for this PC's hardware (printer, scanner, gate code) and for the receipt layout."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.barcode import BarcodeError, decode_payload, encode_payload, looks_like_payload
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.receipt import ReceiptAd, ReceiptContent
from caspian_parking.core.tickets import TicketNumber
from caspian_parking.devices.printer import PrinterError, installed_printers
from caspian_parking.devices.scanner import WedgeScanner
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_ltr
from caspian_parking.services.context import AppContext
from caspian_parking.services.gate_service import HMAC_SECRET
from caspian_parking.services.receipts import reset_receipt_settings
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import show_toast

PRINTER_BACKENDS = ("simulator", "windows", "escpos")
SCANNER_MODES = ("wedge", "serial", "off")
LABEL_KEYS = ("vehicle_type", "entry_date", "entry_time", "ticket_no", "duplicate")


def serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports

        return [port.device for port in list_ports.comports()]
    except Exception:  # pragma: no cover - depends on the machine
        return []


def sample_content(ctx: AppContext, with_ad: bool = True) -> ReceiptContent:
    now = ctx.clock.now_utc()
    key = ctx.secrets.get_or_create(HMAC_SECRET)
    gate = ctx.config.gate_code or 1
    return ReceiptContent(
        kind="entry",
        ticket_no=str(TicketNumber(gate, 1)),
        entry_at=now,
        vehicle_label=tr("vehicle.sedan"),
        plate=parse_plate("12ب345-22"),
        payload=encode_payload(gate, 1, now, key),
        ad=ReceiptAd(tr("receipt.sample_shop"), tr("receipt.sample_location"), tr("receipt.sample_offer"))
        if with_ad
        else None,
        training=ctx.training,
    )


class HardwareTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        devices = ctx.config.devices
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)

        gate_card = Card(tr("hardware.this_pc"))
        row = QHBoxLayout()
        self.gate_code = QSpinBox()
        self.gate_code.setRange(1, 9)
        self.gate_code.setValue(ctx.config.gate_code or 1)
        row.addWidget(label(tr("hardware.gate_code"), "caption"))
        row.addWidget(self.gate_code)
        row.addWidget(label(tr("hardware.gate_code_hint"), "muted", wrap=True), 1)
        gate_card.body().addLayout(row)
        layout.addWidget(gate_card)

        printer_card = Card(tr("hardware.printer"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        self.backend = QComboBox()
        for backend in PRINTER_BACKENDS:
            self.backend.addItem(tr(f"hardware.backend_{backend}"), backend)
        self.backend.setCurrentIndex(max(0, self.backend.findData(devices.printer_backend)))
        self.printer_name = QComboBox()
        self.printer_name.setEditable(True)
        self.printer_name.addItems(installed_printers())
        self.printer_name.setCurrentText(devices.printer_name)
        grid.addWidget(label(tr("hardware.backend"), "caption"), 0, 0)
        grid.addWidget(self.backend, 1, 0)
        grid.addWidget(label(tr("hardware.printer_name"), "caption"), 0, 1)
        grid.addWidget(self.printer_name, 1, 1)
        printer_card.body().addLayout(grid)
        test_row = QHBoxLayout()
        self.printer_status = label("", "muted")
        test_row.addWidget(self.printer_status, 1)
        test_row.addWidget(Button(tr("hardware.test_print"), "printer", on_click=self.test_print))
        printer_card.body().addLayout(test_row)
        layout.addWidget(printer_card)

        scanner_card = Card(tr("hardware.scanner"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        self.scanner_mode = QComboBox()
        for mode in SCANNER_MODES:
            self.scanner_mode.addItem(tr(f"hardware.scanner_{mode}"), mode)
        self.scanner_mode.setCurrentIndex(max(0, self.scanner_mode.findData(devices.scanner_mode)))
        self.scanner_port = QComboBox()
        self.scanner_port.setEditable(True)
        self.scanner_port.addItems(serial_ports())
        self.scanner_port.setCurrentText(devices.scanner_port)
        grid.addWidget(label(tr("hardware.scanner_mode"), "caption"), 0, 0)
        grid.addWidget(self.scanner_mode, 1, 0)
        grid.addWidget(label(tr("hardware.scanner_port"), "caption"), 0, 1)
        grid.addWidget(self.scanner_port, 1, 1)
        scanner_card.body().addLayout(grid)
        scanner_card.add(label(tr("hardware.scanner_test_hint"), "muted", wrap=True))
        self.scan_result = label(tr("hardware.scanner_waiting"), "title", wrap=True)
        scanner_card.add(self.scan_result)
        layout.addWidget(scanner_card)
        self.scanner = WedgeScanner(parent=self)
        self.scanner.scanned.connect(self.show_scan)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(Button(tr("common.save"), "check", variant="primary", on_click=self.save))
        layout.addLayout(save_row)
        layout.addStretch(1)

    def showEvent(self, event: object) -> None:
        self.scanner.install()
        super().showEvent(event)  # type: ignore[arg-type]

    def hideEvent(self, event: object) -> None:
        self.scanner.uninstall()
        super().hideEvent(event)  # type: ignore[arg-type]

    def show_scan(self, text: str) -> str:
        if looks_like_payload(text):
            try:
                payload = decode_payload(text, self.ctx.secrets.get_or_create(HMAC_SECRET))
                result = tr("hardware.scan_ok", gate=payload.gate, time=fa_datetime(payload.entry_utc))
            except BarcodeError:
                result = tr("hardware.scan_forged")
        else:
            result = tr("hardware.scan_text", text=fa_ltr(text))
        self.scan_result.setText(result)
        return result

    def save(self) -> None:
        devices = self.ctx.config.devices
        devices.printer_backend = self.backend.currentData()
        devices.printer_name = self.printer_name.currentText().strip()
        devices.scanner_mode = self.scanner_mode.currentData()
        devices.scanner_port = self.scanner_port.currentText().strip()
        self.ctx.config.gate_code = self.gate_code.value()
        self.ctx.save_config()
        show_toast(self, tr("common.saved"))

    def test_print(self) -> bool:
        self.save()
        printing = ReceiptPrinting(self.ctx)
        status = printing.printer.status()
        self.printer_status.setText(tr("hardware.status_ok") if status.ready else tr("hardware.status_bad"))
        try:
            printing.print_content(sample_content(self.ctx, with_ad=False), "test")
        except PrinterError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("hardware.test_sent"))
        return True


class ReceiptTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        outer = QHBoxLayout(self)
        outer.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        outer.setSpacing(Space.L)
        form = QVBoxLayout()
        form.setSpacing(Space.L)
        sections = Card(tr("receipt.sections"))
        self.show_logo = QCheckBox(tr("receipt.show_logo"))
        self.show_ad = QCheckBox(tr("receipt.show_ad"))
        self.show_art = QCheckBox(tr("receipt.show_art"))
        self.divider_image = QCheckBox(tr("receipt.divider_image"))
        self.exit_receipt = QCheckBox(tr("receipt.exit_default"))
        self.preview_first = QCheckBox(tr("receipt.preview_first"))
        for box in (
            self.show_logo,
            self.show_ad,
            self.show_art,
            self.divider_image,
            self.exit_receipt,
            self.preview_first,
        ):
            sections.add(box)
        art_row = QHBoxLayout()
        art_row.addWidget(label(tr("receipt.art_file"), "caption"))
        self.art = QComboBox()
        art_row.addWidget(self.art, 1)
        sections.body().addLayout(art_row)
        form.addWidget(sections)
        texts = Card(tr("receipt.texts"))
        grid = QGridLayout()
        self.labels: dict[str, TextField] = {}
        for index, key in enumerate(LABEL_KEYS):
            field = TextField(tr(f"receipt.{key}"))
            self.labels[key] = field
            grid.addWidget(field, index // 2, index % 2)
        texts.body().addLayout(grid)
        form.addWidget(texts)
        buttons = QHBoxLayout()
        buttons.addWidget(Button(tr("receipt.open_folder"), "folder-open", variant="ghost", on_click=self.open_folder))
        buttons.addWidget(Button(tr("receipt.reset"), "rotate-ccw", variant="ghost", on_click=self.reset))
        buttons.addStretch(1)
        buttons.addWidget(Button(tr("receipt.refresh_preview"), "eye", on_click=self.refresh_preview))
        buttons.addWidget(Button(tr("common.save"), "check", variant="primary", on_click=self.save))
        form.addLayout(buttons)
        form.addStretch(1)
        outer.addLayout(form, 3)
        preview_card = Card(tr("receipt.preview"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.preview)
        preview_card.add(scroll, 1)
        self.warnings = label("", "muted", wrap=True)
        preview_card.add(self.warnings)
        outer.addWidget(preview_card, 2)
        self.load()

    def load(self) -> None:
        with self.ctx.read() as session:
            self.show_logo.setChecked(bool(get_setting(session, "receipt.show_logo")))
            self.show_ad.setChecked(bool(get_setting(session, "receipt.show_ad")))
            self.show_art.setChecked(bool(get_setting(session, "receipt.show_barcode_art")))
            self.divider_image.setChecked(bool(get_setting(session, "receipt.divider_image")))
            self.exit_receipt.setChecked(bool(get_setting(session, "receipt.print_exit_receipt")))
            self.preview_first.setChecked(bool(get_setting(session, "gate.preview_before_print")))
            art = get_setting(session, "receipt.barcode_art")
            labels = dict(get_setting(session, "receipt.labels") or {})
        ReceiptPrinting(self.ctx)  # makes sure the built-in art exists
        self.art.clear()
        for path in sorted(self.ctx.data_root.barcode_art.glob("*.png")):
            self.art.addItem(path.name, path.name)
        self.art.setCurrentIndex(max(0, self.art.findData(art)))
        for key, field in self.labels.items():
            field.setText(labels.get(key, ""))
        self.refresh_preview()

    def save(self) -> None:
        labels = {key: field.value() for key, field in self.labels.items() if field.value()}
        with self.ctx.uow(reason=tr("settings.receipt")) as session:
            set_setting(session, "receipt.show_logo", self.show_logo.isChecked())
            set_setting(session, "receipt.show_ad", self.show_ad.isChecked())
            set_setting(session, "receipt.show_barcode_art", self.show_art.isChecked())
            set_setting(session, "receipt.divider_image", self.divider_image.isChecked())
            set_setting(session, "receipt.print_exit_receipt", self.exit_receipt.isChecked())
            set_setting(session, "gate.preview_before_print", self.preview_first.isChecked())
            if self.art.currentData():
                set_setting(session, "receipt.barcode_art", self.art.currentData())
            set_setting(session, "receipt.labels", labels)
        self.refresh_preview()
        show_toast(self, tr("common.saved"))

    def reset(self) -> None:
        with self.ctx.uow() as session:
            reset_receipt_settings(session)
        self.load()
        show_toast(self, tr("receipt.reset_done"))

    def refresh_preview(self) -> None:
        result = ReceiptPrinting(self.ctx).render(sample_content(self.ctx))
        self.preview.setPixmap(
            QPixmap.fromImage(result.image).scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        )
        self.warnings.setText("\n".join(tr(w) for w in result.warnings))

    def open_folder(self) -> None:  # pragma: no cover - opens Explorer
        folder = self.ctx.data_root.receipt
        if hasattr(os, "startfile"):
            os.startfile(folder)  # type: ignore[attr-defined]
