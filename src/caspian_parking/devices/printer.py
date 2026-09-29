"""Receipt printers behind one interface (SPEC §6): simulator, Windows driver, raw ESC/POS raster."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter

log = logging.getLogger(__name__)

PAPER_WIDTH_MM = 80
PRINT_DPI = 203
ESC = b"\x1b"
GS = b"\x1d"
RASTER_CHUNK_ROWS = 256


class PrinterError(RuntimeError):
    """Printing failed; ``str(error)`` is an i18n key (printer.offline, printer.paper_out …)."""


@dataclass(frozen=True)
class PrinterStatus:
    online: bool
    paper_ok: bool = True
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.online and self.paper_ok


class Printer(Protocol):
    name: str

    def status(self) -> PrinterStatus: ...

    def print_image(self, image: QImage, job_name: str = "receipt") -> None: ...


# ---------------------------------------------------------------- simulator


class SimulatorPrinter:
    """Saves each receipt as PNG (and keeps it in memory). Can pretend to be offline or out of paper."""

    def __init__(self, folder: Path | None = None, name: str = "simulator") -> None:
        self.name = name
        self.folder = folder
        self.printed: list[QImage] = []
        self.online = True
        self.paper_ok = True

    def status(self) -> PrinterStatus:
        return PrinterStatus(self.online, self.paper_ok)

    def print_image(self, image: QImage, job_name: str = "receipt") -> None:
        if not self.online:
            raise PrinterError("printer.offline")
        if not self.paper_ok:
            raise PrinterError("printer.paper_out")
        self.printed.append(image.copy())
        if self.folder is not None:
            self.folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            image.save(str(self.folder / f"{stamp}_{job_name}.png"))


# ---------------------------------------------------------------- Windows driver


_PRINTER_STATUS_OFFLINE = 0x00000080
_PRINTER_STATUS_ERROR = 0x00000002
_PRINTER_STATUS_PAPER_OUT = 0x00000010
_PRINTER_STATUS_PAPER_PROBLEM = 0x00000040
_PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400


def installed_printers() -> list[str]:
    try:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [row[2] for row in win32print.EnumPrinters(flags)]
    except Exception as exc:  # pragma: no cover - depends on the machine
        log.warning("cannot list printers: %s", exc)
        return []


def windows_status(printer_name: str) -> PrinterStatus:
    try:
        import win32print

        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
        finally:
            win32print.ClosePrinter(handle)
    except Exception as exc:
        return PrinterStatus(False, message=str(exc))
    status = int(info.get("Status", 0))
    attributes = int(info.get("Attributes", 0))
    online = not (status & (_PRINTER_STATUS_OFFLINE | _PRINTER_STATUS_ERROR)) and not (
        attributes & _PRINTER_ATTRIBUTE_WORK_OFFLINE
    )
    paper_ok = not (status & (_PRINTER_STATUS_PAPER_OUT | _PRINTER_STATUS_PAPER_PROBLEM))
    return PrinterStatus(online, paper_ok)


class WindowsDriverPrinter:
    """Prints the 1-bit receipt image through the Windows printer driver (QPrinter)."""

    def __init__(self, printer_name: str) -> None:
        self.name = printer_name

    def status(self) -> PrinterStatus:
        return windows_status(self.name)

    def print_image(self, image: QImage, job_name: str = "receipt") -> None:
        from PySide6.QtPrintSupport import QPrinter

        state = self.status()
        if not state.online:
            raise PrinterError("printer.offline")
        if not state.paper_ok:
            raise PrinterError("printer.paper_out")
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(self.name)
        if not printer.isValid():
            raise PrinterError("printer.not_found")
        printer.setDocName(job_name)
        height_mm = image.height() / PRINT_DPI * 25.4
        page = QPageSize(QSizeF(PAPER_WIDTH_MM, height_mm), QPageSize.Unit.Millimeter, "receipt")
        printer.setPageLayout(QPageLayout(page, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
        printer.setFullPage(True)
        painter = QPainter()
        if not painter.begin(printer):
            raise PrinterError("printer.offline")
        try:
            target = printer.pageLayout().fullRectPixels(printer.resolution())
            scale = target.width() / image.width()
            painter.drawImage(QRectF(0, 0, target.width(), image.height() * scale), image)
        finally:
            painter.end()


# ---------------------------------------------------------------- ESC/POS raster


def escpos_raster(image: QImage, feed_lines: int = 4, cut: bool = True) -> bytes:
    """``GS v 0`` raster commands for a 1-bit image (black = 1)."""
    mono = image.convertToFormat(QImage.Format.Format_Mono, Qt.ImageConversionFlag.ThresholdDither)
    width_bytes = (mono.width() + 7) // 8
    black_index = 0 if mono.color(0) == 0xFF000000 else 1
    data = bytearray(ESC + b"@")
    for start in range(0, mono.height(), RASTER_CHUNK_ROWS):
        rows = min(RASTER_CHUNK_ROWS, mono.height() - start)
        data += GS + b"v0\x00" + bytes([width_bytes & 0xFF, width_bytes >> 8, rows & 0xFF, rows >> 8])
        for y in range(start, start + rows):
            row = bytearray(width_bytes)
            for x in range(mono.width()):
                if mono.pixelIndex(x, y) == black_index:
                    row[x >> 3] |= 0x80 >> (x & 7)
            data += row
    data += ESC + b"d" + bytes([feed_lines])
    if cut:
        data += GS + b"V" + bytes([66, 0])
    return bytes(data)


class EscPosRawPrinter:
    """Sends ESC/POS raster data as a RAW job to a Windows printer queue (for printers that need it)."""

    def __init__(self, printer_name: str) -> None:
        self.name = printer_name

    def status(self) -> PrinterStatus:
        return windows_status(self.name)

    def print_image(self, image: QImage, job_name: str = "receipt") -> None:
        import win32print

        state = self.status()
        if not state.online:
            raise PrinterError("printer.offline")
        payload = escpos_raster(image)
        try:
            handle = win32print.OpenPrinter(self.name)
        except Exception as exc:
            raise PrinterError("printer.not_found") from exc
        try:
            win32print.StartDocPrinter(handle, 1, (job_name, None, "RAW"))
            try:
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, payload)
                win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        except Exception as exc:
            raise PrinterError("printer.failed") from exc
        finally:
            win32print.ClosePrinter(handle)


def create_printer(backend: str, printer_name: str, simulator_folder: Path | None) -> Printer:
    if backend == "windows" and printer_name:
        return WindowsDriverPrinter(printer_name)
    if backend == "escpos" and printer_name:
        return EscPosRawPrinter(printer_name)
    return SimulatorPrinter(simulator_folder)
