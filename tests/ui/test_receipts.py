"""Receipt rendering (SPEC §5) + printers. Receipts are saved to tests/artifacts for visual review."""

from __future__ import annotations

import io
import time as _time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from caspian_parking.core.barcode import decode_payload, encode_payload
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.receipt import ReceiptAd, ReceiptContent, ReceiptLayout
from caspian_parking.devices.printer import (
    PrinterError,
    SimulatorPrinter,
    create_printer,
    escpos_raster,
)
from caspian_parking.ui.receipt.assets import draw_forest, draw_wave, ensure_default_assets
from caspian_parking.ui.receipt.renderer import WIDTH, render_receipt

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
KEY = b"k" * 32
ENTRY = datetime(2026, 9, 28, 11, 2, tzinfo=UTC)  # 14:32 Tehran
PAYLOAD = encode_payload(1, 24715, ENTRY, KEY)
AD = ReceiptAd("مبلمان آرتا", "طبقه همکف، واحد ۱۲", "۲۰٪ تخفیف ویژه پاییز")


@pytest.fixture
def assets(tmp_path, themed):
    art_dir = tmp_path / "barcode-art"
    ensure_default_assets(art_dir)
    logo = tmp_path / "logo.png"
    _fake_logo().save(str(logo))
    return tmp_path, art_dir / "tree.png", logo


def _fake_logo() -> QImage:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter

    image = QImage(400, 150, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(20, 50, 74))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(140, 10, 120, 120)
    painter.end()
    return image


def _content(**overrides) -> ReceiptContent:
    data = {
        "kind": "entry",
        "ticket_no": "1-24715-4",
        "entry_at": ENTRY,
        "vehicle_label": "سواری",
        "plate": parse_plate("12ب345-22"),
        "payload": PAYLOAD,
    }
    data.update(overrides)
    return ReceiptContent(**data)


def _layout(assets, **overrides) -> ReceiptLayout:
    _root, art, logo = assets
    data = {"logo_path": logo, "barcode_art_path": art}
    data.update(overrides)
    return ReceiptLayout(**data)


def _decode(image: QImage) -> list[str]:
    """Scan the rendered receipt with a real barcode reader (zxing-cpp)."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    pil = Image.open(io.BytesIO(bytes(buffer.data()))).convert("L")
    return [r.text for r in zxingcpp.read_barcodes(pil) if r.format == zxingcpp.BarcodeFormat.Code128]


def save(result, name: str) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    result.image.save(str(ARTIFACTS / f"receipt_{name}.png"))


def test_mother_receipt_without_ad(assets):
    result = render_receipt(_content(), _layout(assets))
    save(result, "entry_no_ad")
    image = result.image
    assert image.width() == WIDTH
    assert image.format() == QImage.Format.Format_Mono
    assert 700 < image.height() < 1600
    assert result.sections == ["logo", "vehicle", "barcode"]
    assert result.dividers == 2  # one between logo and vehicle info, one before the barcode
    assert result.module_px >= 3
    assert result.warnings == []


def test_mother_receipt_with_ad_has_extra_divider(assets):
    without = render_receipt(_content(), _layout(assets))
    result = render_receipt(_content(ad=AD), _layout(assets))
    save(result, "entry_with_ad")
    assert result.sections == ["logo", "ad", "vehicle", "barcode"]
    assert result.dividers == 3
    assert result.image.height() > without.image.height()


def test_printed_barcode_scans_back_to_the_payload(assets):
    result = render_receipt(_content(ad=AD), _layout(assets))
    decoded = _decode(result.image)
    assert decoded == [PAYLOAD]
    payload = decode_payload(decoded[0], KEY)
    assert payload.entry_utc == ENTRY
    assert payload.sequence_mod == 24715


def test_barcode_scans_with_wave_art_and_without_art(assets):
    root, _art, logo = assets
    wave = root / "barcode-art" / "wave.png"
    for name, layout in (
        ("wave", _layout(assets, barcode_art_path=wave)),
        ("no_art", _layout(assets, show_barcode_art=False)),
    ):
        result = render_receipt(_content(), layout)
        save(result, f"entry_{name}")
        assert _decode(result.image) == [PAYLOAD]
    del logo


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("duplicate", {"kind": "duplicate"}),
        ("motorcycle", {"plate": parse_plate("123-45678"), "vehicle_label": "موتورسیکلت"}),
        ("no_plate", {"plate": None}),
        ("training", {"training": True}),
    ],
)
def test_receipt_variants(assets, name, overrides):
    result = render_receipt(_content(**overrides), _layout(assets))
    save(result, name)
    assert result.image.width() == WIDTH
    assert _decode(result.image) == [PAYLOAD]


def test_exit_receipt_has_no_barcode(assets):
    content = _content(
        kind="exit",
        payload=None,
        exit_at=ENTRY.replace(hour=13),
        duration_minutes=118,
        amount=380_000,
        method_label="نقدی",
        ad=AD,
    )
    result = render_receipt(content, _layout(assets))
    save(result, "exit")
    assert "barcode" not in result.sections
    assert _decode(result.image) == []


def test_missing_images_are_skipped_with_warnings(assets, tmp_path):
    layout = ReceiptLayout(
        logo_path=tmp_path / "nope.png",
        barcode_art_path=tmp_path / "nope-art.png",
        divider_path=tmp_path / "nope-div.png",
        template_path=tmp_path / "nope-template.png",
    )
    result = render_receipt(_content(), layout)
    save(result, "missing_images")
    assert set(result.warnings) == {
        "receipt.logo_missing",
        "receipt.art_missing",
        "receipt.divider_missing",
        "receipt.template_missing",
    }
    assert _decode(result.image) == [PAYLOAD]


def test_sections_can_be_turned_off(assets):
    result = render_receipt(_content(ad=AD), _layout(assets, show_logo=False, show_ad=False))
    assert result.sections == ["vehicle", "barcode"]
    assert result.dividers == 2


def test_custom_template_replaces_logo_and_ad(assets, tmp_path):
    template = tmp_path / "templates" / "sale.png"
    template.parent.mkdir()
    _fake_logo().scaled(576, 200).save(str(template))
    result = render_receipt(_content(ad=AD), _layout(assets, template_path=template))
    save(result, "template")
    assert result.sections == ["template", "vehicle", "barcode"]


def test_custom_labels(assets):
    result = render_receipt(_content(), _layout(assets, labels={"ticket_no": "کد"}))
    assert result.image.width() == WIDTH


def test_default_art_files(tmp_path, themed):
    written = ensure_default_assets(tmp_path)
    assert {p.name for p in written} == {"tree.png", "wave.png"}
    assert ensure_default_assets(tmp_path) == []
    assert draw_forest().width() == 540
    assert not draw_wave().isNull()


@pytest.mark.serial
def test_render_and_print_is_fast(assets):
    printer = SimulatorPrinter()
    started = _time.perf_counter()
    result = render_receipt(_content(ad=AD), _layout(assets))
    printer.print_image(result.image)
    assert (_time.perf_counter() - started) < 1.0  # SPEC §2.4: entry receipt < 1 s
    assert len(printer.printed) == 1


def test_simulator_printer_errors_and_files(tmp_path, themed):
    printer = SimulatorPrinter(tmp_path / "printed")
    image = QImage(576, 100, QImage.Format.Format_Mono)
    image.fill(1)
    printer.print_image(image, "entry")
    assert len(list((tmp_path / "printed").glob("*_entry.png"))) == 1
    printer.paper_ok = False
    assert not printer.status().ready
    with pytest.raises(PrinterError, match="paper_out"):
        printer.print_image(image)
    printer.online = False
    with pytest.raises(PrinterError, match="offline"):
        printer.print_image(image)


def test_escpos_raster_bytes(themed):
    image = QImage(16, 3, QImage.Format.Format_Mono)
    image.fill(1)  # index 1 = white for a fresh mono image with default colour table
    image.setColorTable([0xFFFFFFFF, 0xFF000000])
    image.fill(0)
    image.setPixel(0, 0, 1)  # one black pixel top-left
    data = escpos_raster(image, feed_lines=2, cut=True)
    assert data.startswith(b"\x1b@\x1dv0\x00\x02\x00\x03\x00")
    assert data[10] == 0x80  # ESC @ (2) + GS v 0 m (4) + xL xH yL yH (4): first raster byte, leftmost pixel black
    assert data[11] == 0x00
    assert data.endswith(b"\x1bd\x02\x1dVB\x00")


def test_printer_factory_defaults_to_simulator(tmp_path):
    assert isinstance(create_printer("simulator", "", tmp_path), SimulatorPrinter)
    assert isinstance(create_printer("windows", "", tmp_path), SimulatorPrinter)
    assert create_printer("windows", "X", None).name == "X"
    assert create_printer("escpos", "Y", None).name == "Y"
