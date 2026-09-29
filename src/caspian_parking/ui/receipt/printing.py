"""Receipt printing for this PC: settings → layout → 1-bit image → configured printer."""

from __future__ import annotations

import logging
from pathlib import Path

from caspian_parking.core.receipt import ReceiptContent, ReceiptLayout
from caspian_parking.devices.printer import Printer, PrinterError, SimulatorPrinter, create_printer
from caspian_parking.services.context import AppContext
from caspian_parking.services.receipts import load_layout
from caspian_parking.ui.receipt.assets import ensure_default_assets
from caspian_parking.ui.receipt.renderer import RenderResult, render_receipt

log = logging.getLogger(__name__)

SIMULATOR_KEEP_FILES = 200


class ReceiptPrinting:
    def __init__(self, ctx: AppContext, printer: Printer | None = None) -> None:
        self.ctx = ctx
        ensure_default_assets(ctx.data_root.barcode_art)
        self._override = printer
        self._configured: Printer | None = None
        self._configured_key: tuple[str, str] | None = None
        self.last: RenderResult | None = None

    @property
    def printer(self) -> Printer:
        """The injected printer, or the one configured for this PC (follows setting changes)."""
        if self._override is not None:
            return self._override
        devices = self.ctx.config.devices
        key = (devices.printer_backend, devices.printer_name)
        if self._configured is None or key != self._configured_key:
            self._configured = create_printer(*key, self.ctx.data_root.logs / "printed")
            self._configured_key = key
        return self._configured

    @printer.setter
    def printer(self, value: Printer) -> None:
        self._override = value

    def layout(self, template_path: Path | None = None) -> ReceiptLayout:
        with self.ctx.read() as session:
            return load_layout(session, self.ctx.data_root, template_path)

    def render(self, content: ReceiptContent, template_path: Path | None = None) -> RenderResult:
        result = render_receipt(content, self.layout(template_path))
        self.last = result
        return result

    def print_result(self, result: RenderResult, job_name: str = "receipt") -> None:
        """Send an already rendered receipt to the printer (raises PrinterError)."""
        self.printer.print_image(result.image, job_name)
        if isinstance(self.printer, SimulatorPrinter) and self.printer.folder is not None:
            _prune(self.printer.folder)

    def print_content(self, content: ReceiptContent, job_name: str = "receipt") -> RenderResult:
        result = self.render(content)
        self.print_result(result, job_name)
        return result


def _prune(folder: Path) -> None:
    files = sorted(folder.glob("*.png"))
    for old in files[:-SIMULATOR_KEEP_FILES]:
        try:
            old.unlink()
        except OSError as exc:  # pragma: no cover
            log.debug("cannot prune %s: %s", old, exc)


__all__ = ["PrinterError", "ReceiptPrinting"]
