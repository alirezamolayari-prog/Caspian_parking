"""Phase 7 UI: coupon receipt, ads on gate receipts, coupon at exit, templates, ads screen, slideshow."""

from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from sqlalchemy import func, select

from caspian_parking.core.coupons import new_coupon_code
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.receipt import CouponReceipt, ReceiptLayout
from caspian_parking.data.models import AdPrint, CouponRedemption
from caspian_parking.devices.adscreen import SimulatorAdScreen, SlideRotation
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services import templates
from caspian_parking.services.ads import AdService
from caspian_parking.services.coupons import CouponService
from caspian_parking.services.gate_service import PaymentMethod
from caspian_parking.services.people import PeopleService
from caspian_parking.services.settings import set_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.receipt.renderer import WIDTH, render_coupon
from caspian_parking.ui.screens.ads import AdsScreen
from caspian_parking.ui.screens.gate import GateScreen
from caspian_parking.ui.widgets.slideshow import SlideshowWindow

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
MON = date(2026, 9, 28)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


def _decode(image: QImage) -> list[str]:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    pil = Image.open(io.BytesIO(bytes(buffer.data()))).convert("L")
    return [r.text for r in zxingcpp.read_barcodes(pil) if r.format == zxingcpp.BarcodeFormat.Code128]


def _image(path: Path, color: str = "#2255aa", size: tuple[int, int] = (576, 200)) -> Path:
    image = QImage(*size, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return path


@pytest.fixture
def ctx(admin_ctx, clock):
    clock.set(at(MON, 10))
    with admin_ctx.uow() as session:
        set_setting(session, "coupons.unit_price", 150_000)
    return admin_ctx


@pytest.fixture
def shop(ctx):
    return PeopleService(ctx).create_shop("مبلمان آرتا", unit="همکف ۱۲")


@pytest.fixture
def gate(qtbot, ctx):
    widget = GateScreen(ctx, ReceiptPrinting(ctx, SimulatorPrinter()))
    qtbot.addWidget(widget)
    widget.resize(1500, 900)
    widget.show()
    return widget


# ---------------------------------------------------------------- coupon receipt


def test_coupon_receipt_renders_and_scans(themed, tmp_path):
    code = new_coupon_code()
    result = render_coupon(CouponReceipt(code, "مبلمان آرتا", MON + timedelta(days=30)), ReceiptLayout())
    ARTIFACTS.mkdir(exist_ok=True)
    result.image.save(str(ARTIFACTS / "receipt_coupon.png"))
    assert result.image.width() == WIDTH
    assert result.sections == ["shop", "barcode"]
    assert result.module_px >= 3
    assert _decode(result.image) == [code]
    template = _image(tmp_path / "shop.png")
    with_template = render_coupon(CouponReceipt(code, "x", MON, template), ReceiptLayout())
    with_template.image.save(str(ARTIFACTS / "receipt_coupon_template.png"))
    assert with_template.sections == ["template", "barcode"]
    missing = render_coupon(CouponReceipt(code, "x", MON, tmp_path / "gone.png"), ReceiptLayout())
    assert missing.warnings == ["receipt.template_missing"]
    assert missing.sections == ["shop", "barcode"]


# ---------------------------------------------------------------- gate integration


def test_entry_receipts_rotate_ads_and_count_prints(gate, ctx, shop):
    ads = AdService(ctx)
    first = ads.create_ad(shop.id, "bronze", MON, MON, offer="الف")
    second = ads.create_ad(shop.id, "bronze", MON, MON, offer="ب")
    for plate in ("12ب345-22", "55ج777-11"):
        assert gate.plate_input.set_text(plate)
        assert gate.print_entry() is not None
    assert "ad" in gate.printing.last.sections
    assert ads.print_counts() == {first.id: 1, second.id: 1}
    with ctx.uow() as session:
        set_setting(session, "receipt.show_ad", False)
    assert gate.plate_input.set_text("31د456-44")
    gate.print_entry()
    assert "ad" not in gate.printing.last.sections
    assert gate.printing.last.dividers == 2
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(AdPrint)) == 2


def test_exit_receipt_carries_exit_ad(gate, ctx, shop, clock):
    ad = AdService(ctx).create_ad(shop.id, "bronze", MON, MON, offer="خروج", on_entry=False, on_exit=True)
    assert gate.plate_input.set_text("12ب345-22")
    entry = gate.print_entry()
    assert "ad" not in gate.printing.last.sections
    clock.advance(minutes=30)
    gate.on_scanned(entry.payload)
    gate.exit_receipt.setChecked(True)
    assert gate.pay(PaymentMethod.CASH)
    assert "ad" in gate.printing.last.sections
    assert AdService(ctx).print_counts() == {ad.id: 1}


def test_coupon_scanned_at_exit(gate, ctx, shop, clock):
    _batch, coupons = CouponService(ctx).sell_batch(shop.id, 1, "cash")
    assert gate.plate_input.set_text("12ب345-22")
    entry = gate.print_entry()
    clock.advance(minutes=90)
    gate.on_scanned(coupons[0].code)  # coupon before ticket → hint only
    assert gate.quote is None
    gate.on_scanned(entry.payload)
    assert gate.quote.amount_due > 0
    gate.on_scanned(coupons[0].code)
    assert gate.quote.amount_due == 0
    assert gate.quote.coupon_id == coupons[0].id
    assert gate.pay(PaymentMethod.CASH)
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(CouponRedemption)) == 1


def test_typed_invalid_coupon_keeps_quote(gate, ctx, clock):
    assert gate.plate_input.set_text("12ب345-22")
    entry = gate.print_entry()
    clock.advance(minutes=90)
    gate.on_scanned(entry.payload)
    due = gate.quote.amount_due
    gate.coupon_field.setText("900000000008")
    assert gate.apply_coupon() is None
    assert gate.quote.amount_due == due


def test_selected_template_replaces_logo_and_missing_file_warns(qtbot, ctx):
    from caspian_parking.app import build_main_window

    window = build_main_window(ctx)
    qtbot.addWidget(window)
    gate = window.show_screen("gate")
    gate.printing.printer = SimulatorPrinter()
    template = _image(ctx.data_root.templates / "یلدا.png")
    templates.select_template(ctx, "یلدا.png")
    assert gate.plate_input.set_text("12ب345-22")
    gate.print_entry()
    assert gate.printing.last.sections[0] == "template"
    template.unlink()
    assert gate.plate_input.set_text("55ج777-11")
    gate.print_entry()
    assert "template" not in gate.printing.last.sections  # back to the mother receipt
    assert any(a.key == "template" for a in window.alerts.alerts())


# ---------------------------------------------------------------- ads screen


@pytest.fixture
def screen(qtbot, ctx, shop):
    widget = AdsScreen(ctx)
    widget.printing.printer = SimulatorPrinter()
    qtbot.addWidget(widget)
    widget.on_show()
    return widget


def test_create_edit_and_end_ad(screen, ctx, shop, tmp_path):
    screen.ad_shop.setCurrentIndex(screen.ad_shop.findData(shop.id))
    screen.ad_package.setCurrentIndex(screen.ad_package.findData("gold"))
    assert screen.ad_price.value() == 25_000_000
    assert screen.ad_logo_button.isEnabled()
    screen.ad_offer.setText("۲۰٪ تخفیف")
    screen.ad_on_exit.setChecked(True)
    screen.ad_days[3].setChecked(True)
    screen.ad_method.setCurrentIndex(screen.ad_method.findData("card"))
    screen.pick_logo(_image(tmp_path / "logo.png"))
    ad = screen.save_ad()
    assert ad is not None
    assert (ad.package, ad.offer, ad.on_exit, ad.weekdays) == ("gold", "20٪ تخفیف", True, [3])
    assert ad.logo_file == "logos/logo.png"
    assert screen.ads_model.loaded_count() == 1
    screen.ad_offer.setText("۳۰٪ تخفیف")
    assert screen.save_ad().offer == "30٪ تخفیف"
    assert screen.end_current(reason="لغو")
    assert AdService(ctx).active_ads(MON) == []
    screen.ad_package.setCurrentIndex(screen.ad_package.findData("bronze"))
    assert not screen.ad_logo_button.isEnabled()


def test_sell_and_print_coupons(screen, ctx, shop):
    screen.coupon_shop.setCurrentIndex(screen.coupon_shop.findData(shop.id))
    screen.coupon_quantity.setValue(7)
    assert "۱٬۰۵۰٬۰۰۰" in screen.coupon_price.text()
    batch = screen.sell()
    assert batch is not None
    assert screen.pending_coupons() == 7
    screen.flush_coupons()
    printed = screen.printing.printer.printed
    assert len(printed) == 7
    assert all(image.width() == WIDTH for image in printed)
    assert screen.code_model.loaded_count() == 7
    assert screen.stats_model.loaded_count() == 1
    screen.batch_table.selectRow(0)
    assert screen.reprint_batch() == 7
    screen.coupon_method.setCurrentIndex(screen.coupon_method.findData("wallet"))
    assert screen.sell() is None  # empty wallet


def test_calendar_and_raffle_tabs(screen, ctx, shop, clock):
    AdService(ctx).create_ad(shop.id, "bronze", MON, MON)
    days = screen.load_calendar()
    assert len(days) == 30
    assert screen.calendar_model.loaded_count() == 30
    assert "۱" in screen.cal_summary.text()
    assert screen.draw() is None  # no receipts yet
    gate = GateScreen(ctx, ReceiptPrinting(ctx, SimulatorPrinter()))
    ticket = gate.gate.register_entry(parse_plate("12ب345-22")).session.ticket_no
    screen.raffle_sponsor.setCurrentIndex(screen.raffle_sponsor.findData(shop.id))
    assert screen.draw() == ticket
    assert screen.raffle_model.loaded_count() == 1
    gate.deleteLater()


def test_templates_tab_follows_folder(screen, ctx, qtbot):
    folder = ctx.data_root.templates
    (folder / "مناسبت").mkdir()
    _image(folder / "مناسبت" / "نوروز.png")
    _image(folder / "ساده.jpg", "#000000")
    qtbot.waitUntil(lambda: len(screen._templates) == 2, timeout=5000)
    assert screen.template_category.count() == 3  # all + two categories
    assert screen.coupon_template.count() == 3  # plain + two templates
    screen.template_search.setText("نوروز")
    assert [t.name for t in screen._visible_templates] == ["نوروز"]
    screen.template_table.selectRow(0)
    pixmap = screen.preview_template(screen.template_table.selected_object())
    assert pixmap is not None
    assert pixmap.toImage().convertToFormat(QImage.Format.Format_Mono).width() == pixmap.width()
    assert screen.use_selected()
    assert templates.selected_template(ctx).relative == "مناسبت/نوروز.png"
    assert "نوروز" in screen.selected_label.text()
    assert screen.test_print()
    assert screen.printing.last.sections[0] == "template"
    (folder / "مناسبت" / "نوروز.png").unlink()
    qtbot.waitUntil(lambda: len(screen._templates) == 1, timeout=5000)
    assert "پیدا نشد" in screen.selected_label.text()
    screen.use_mother()
    assert templates.selected_template(ctx).path is None


def test_operator_has_no_ads_screen(qtbot, operator_ctx):
    from caspian_parking.app import build_main_window

    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    assert window.show_screen("ads") is None


# ---------------------------------------------------------------- slideshow


def test_slide_rotation_follows_folder(tmp_path):
    folder = tmp_path / "slides"
    folder.mkdir()
    rotation = SlideRotation(folder)
    assert rotation.next() is None
    for name in ("b.png", "a.png", "notes.txt"):
        (folder / name).write_bytes(b"x")
    rotation.rescan()
    assert [p.name for p in rotation.slides] == ["a.png", "b.png"]
    assert [rotation.next().name for _ in range(3)] == ["a.png", "b.png", "a.png"]
    (folder / "a.png").unlink()
    rotation.rescan()
    assert rotation.next().name == "b.png"
    screen = SimulatorAdScreen()
    screen.show_slide(rotation.current)
    screen.close()
    assert screen.shown == [folder / "b.png"] and screen.closed


def test_slideshow_window(qtbot, ctx, tmp_path):
    folder = ctx.data_root.slideshow
    _image(folder / "1.png")
    _image(folder / "2.png", "#aa2222")
    window = SlideshowWindow(folder, 1)
    qtbot.addWidget(window)
    window.show_on_best_screen()
    assert window.current.name == "1.png"
    assert not window.image.pixmap().isNull()
    assert window.advance().name == "2.png"
    qtbot.waitUntil(lambda: window.current.name == "1.png", timeout=3000)  # timer advances
    for path in list(folder.iterdir()):
        path.unlink()
    qtbot.waitUntil(lambda: window.current is None, timeout=5000)
    assert window.image.text()
    window.set_seconds(5)
    assert window.timer.interval() == 5000
    assert window.windowFlags() & Qt.WindowType.Window
