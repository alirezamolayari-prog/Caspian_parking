"""Receipt renderer (SPEC §5): QPainter → 576 px wide → 1-bit image.

The same renderer feeds the on-screen preview and the printer, so what you see is what prints.
Thermal printers cannot shape Persian text, therefore the whole receipt is an image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF

from caspian_parking.core.barcode import QUIET_ZONE_MODULES, code128c_modules
from caspian_parking.core.digits import to_persian_digits
from caspian_parking.core.plate import Plate, PlateKind
from caspian_parking.core.receipt import ReceiptAd, ReceiptContent, ReceiptLayout
from caspian_parking.i18n import tr
from caspian_parking.i18n.bidi import ltr
from caspian_parking.i18n.format import fa_date, fa_duration, fa_money, fa_time
from caspian_parking.ui.theme.tokens import FONT_FAMILY, RECEIPT_INK, RECEIPT_PAPER
from caspian_parking.ui.widgets.plate import CAR_ASPECT, MOTO_ASPECT, PlateStyle, paint_plate

WIDTH = 576  # 80 mm paper at 203 dpi
MARGIN = 16
CANVAS_HEIGHT = 2600
MIN_MODULE_PX = 3
BAR_HEIGHT = 104
LOGO_MAX = (440, 190)
ART_MAX_HEIGHT = 150
DIVIDER_HEIGHT = 34


@dataclass
class RenderResult:
    image: QImage
    warnings: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    dividers: int = 0
    module_px: int = 0


def _font(pixels: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(round(pixels))
    font.setWeight(weight)
    return font


def load_mono_image(path: Path | None, max_w: int, max_h: int) -> QImage | None:
    """Load an image, scale it to fit, and dither it to black & white (what the printer can do)."""
    if path is None or not path.is_file():
        return None
    image = QImage(str(path))
    if image.isNull():
        return None
    if image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format.Format_ARGB32)
        flat.fill(QColor(RECEIPT_PAPER))
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    scaled = image.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    mono = scaled.convertToFormat(QImage.Format.Format_Mono, Qt.ImageConversionFlag.DiffuseDither)
    return mono.convertToFormat(QImage.Format.Format_ARGB32)


class _Canvas:
    def __init__(self) -> None:
        self.image = QImage(WIDTH, CANVAS_HEIGHT, QImage.Format.Format_ARGB32)
        self.image.fill(QColor(RECEIPT_PAPER))
        self.painter = QPainter(self.image)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.ink = QColor(RECEIPT_INK)
        self.y: float = MARGIN

    def space(self, pixels: int) -> None:
        self.y += pixels

    def text(self, text: str, pixels: float, weight: QFont.Weight = QFont.Weight.Normal, gap: int = 6) -> None:
        font = _font(pixels, weight)
        self.painter.setFont(font)
        self.painter.setPen(self.ink)
        metrics = QFontMetricsF(font)
        rect = QRectF(MARGIN, self.y, WIDTH - 2 * MARGIN, 2000)
        bounds = self.painter.boundingRect(rect, Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, text)
        self.painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, text)
        self.y += max(bounds.height(), metrics.height()) + gap

    def image_centered(self, image: QImage, gap: int = 8) -> None:
        x = (WIDTH - image.width()) / 2
        self.painter.drawImage(QPointF(x, self.y), image)
        self.y += image.height() + gap

    def finish(self, bottom_margin: int = 40) -> QImage:
        self.painter.end()
        height = min(CANVAS_HEIGHT, int(self.y) + bottom_margin)
        cropped = self.image.copy(0, 0, WIDTH, height)
        return cropped.convertToFormat(QImage.Format.Format_Mono, Qt.ImageConversionFlag.ThresholdDither)


def _divider(canvas: _Canvas, layout: ReceiptLayout, result: RenderResult) -> None:
    image = load_mono_image(layout.divider_path, WIDTH - 2 * MARGIN, 60) if layout.divider_path else None
    if layout.divider_path and image is None:
        result.warnings.append("receipt.divider_missing")
    if image is not None:
        canvas.image_centered(image, gap=10)
    else:
        _builtin_divider(canvas)
    result.dividers += 1


def _builtin_divider(canvas: _Canvas) -> None:
    """line — small diamond — big diamond — small diamond — line (approved design)."""
    painter = canvas.painter
    cx = WIDTH / 2
    cy = canvas.y + DIVIDER_HEIGHT / 2
    painter.setPen(QPen(canvas.ink, 2))
    painter.setBrush(canvas.ink)

    def diamond(center_x: float, half: float) -> None:
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center_x, cy - half),
                    QPointF(center_x + half, cy),
                    QPointF(center_x, cy + half),
                    QPointF(center_x - half, cy),
                ]
            )
        )

    diamond(cx, 10)
    diamond(cx - 26, 5)
    diamond(cx + 26, 5)
    painter.drawLine(QPointF(MARGIN + 24, cy), QPointF(cx - 38, cy))
    painter.drawLine(QPointF(cx + 38, cy), QPointF(WIDTH - MARGIN - 24, cy))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    canvas.y += DIVIDER_HEIGHT + 6


def _label(layout: ReceiptLayout, key: str) -> str:
    return layout.labels.get(key) or tr(f"receipt.{key}")


def _leader_row(canvas: _Canvas, label: str, value: str) -> None:
    """``label ........ value`` — label on the right (RTL), value on the left, dotted leader between."""
    painter = canvas.painter
    font = _font(27, QFont.Weight.DemiBold)
    value_font = _font(29, QFont.Weight.Bold)
    painter.setPen(canvas.ink)
    height = max(QFontMetricsF(font).height(), QFontMetricsF(value_font).height())
    left, right = MARGIN + 8, WIDTH - MARGIN - 8
    row = QRectF(left, canvas.y, right - left, height)
    painter.setFont(font)
    right_flags = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
    label_box = painter.boundingRect(row, right_flags, label)
    painter.drawText(row, right_flags, label)
    painter.setFont(value_font)
    left_flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
    value_box = painter.boundingRect(row, left_flags, value)
    painter.drawText(row, left_flags, value)
    dot_y = canvas.y + height * 0.68
    x = value_box.right() + 14
    stop = label_box.left() - 12
    painter.setPen(QPen(canvas.ink, 2))
    while x < stop:
        painter.drawPoint(QPointF(x, dot_y))
        x += 8
    canvas.y += height + 10


def _plate(canvas: _Canvas, plate: Plate | None) -> None:
    height: float
    width: float
    if plate is not None and plate.kind is PlateKind.MOTORCYCLE:
        height = 128.0
        width = height * MOTO_ASPECT
    else:
        width = 460.0
        height = width / CAR_ASPECT
    rect = QRectF((WIDTH - width) / 2, canvas.y, width, height)
    paint_plate(canvas.painter, rect, plate, PlateStyle.monochrome())
    canvas.y += height + 18


def _ad(canvas: _Canvas, ad: ReceiptAd, result: RenderResult) -> None:
    if ad.logo_path is not None:
        logo = load_mono_image(ad.logo_path, 260, 110)
        if logo is None:
            result.warnings.append("receipt.ad_logo_missing")
        else:
            canvas.image_centered(logo)
    canvas.text(ad.shop_name, 40 if ad.large else 34, QFont.Weight.ExtraBold, gap=2)
    if ad.location:
        canvas.text(ad.location, 25, QFont.Weight.Medium, gap=8)
    if ad.offer:
        _offer_pill(canvas, ad.offer, large=ad.large)
    result.sections.append("ad")


def _offer_pill(canvas: _Canvas, text: str, large: bool = False) -> None:
    """Highlighted offer: white text on a black rounded pill (inverted)."""
    painter = canvas.painter
    font = _font(30 if large else 27, QFont.Weight.Bold)
    metrics = QFontMetricsF(font)
    max_width = WIDTH - 2 * MARGIN - 20
    painter.setFont(font)
    text_rect = painter.boundingRect(
        QRectF(0, 0, max_width - 36, 1000), Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, text
    )
    width = min(max_width, text_rect.width() + 44)
    height = max(metrics.height(), text_rect.height()) + 18
    pill = QRectF((WIDTH - width) / 2, canvas.y, width, height)
    path = QPainterPath()
    path.addRoundedRect(pill, height / 2, height / 2)
    painter.fillPath(path, canvas.ink)
    painter.setPen(QColor(RECEIPT_PAPER))
    painter.drawText(pill.adjusted(18, 9, -18, -9), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
    painter.setPen(canvas.ink)
    canvas.y += height + 10


def _barcode(canvas: _Canvas, payload: str, art: QImage | None, result: RenderResult) -> None:
    modules = code128c_modules(payload)
    total = len(modules) + 2 * QUIET_ZONE_MODULES
    module = WIDTH // total
    if module < MIN_MODULE_PX:
        raise ValueError("barcode does not fit the paper width")
    result.module_px = module
    bars_width = len(modules) * module
    x0 = (WIDTH - bars_width) // 2
    if art is not None:
        # the art sits directly on top of the bars (quiet zones are never covered)
        art_x = (WIDTH - art.width()) / 2
        canvas.painter.drawImage(QPointF(art_x, canvas.y), art)
        canvas.y += art.height()
    top = int(canvas.y)
    for index, bar in enumerate(modules):
        if bar:
            canvas.painter.fillRect(x0 + index * module, top, module, BAR_HEIGHT, canvas.ink)
    canvas.y += BAR_HEIGHT + 8


def render_receipt(content: ReceiptContent, layout: ReceiptLayout) -> RenderResult:
    canvas = _Canvas()
    result = RenderResult(QImage())
    if content.training:
        _training_banner(canvas)

    template = load_mono_image(layout.template_path, WIDTH - 2 * MARGIN, 700) if layout.template_path else None
    if layout.template_path and template is None:
        result.warnings.append("receipt.template_missing")
    if template is not None:
        canvas.image_centered(template, gap=10)
        result.sections.append("template")
    elif layout.show_logo:
        logo = load_mono_image(layout.logo_path, *LOGO_MAX)
        if logo is None:
            result.warnings.append("receipt.logo_missing")
        else:
            canvas.image_centered(logo, gap=10)
            result.sections.append("logo")

    if content.kind == "duplicate":
        _duplicate_label(canvas, _label(layout, "duplicate"))

    _divider(canvas, layout, result)
    if template is None and layout.show_ad and content.ad is not None:
        _ad(canvas, content.ad, result)
        _divider(canvas, layout, result)

    _plate(canvas, content.plate)
    result.sections.append("vehicle")
    _leader_row(canvas, _label(layout, "vehicle_type"), content.vehicle_label)
    _leader_row(canvas, _label(layout, "entry_date"), fa_date(content.entry_at))
    _leader_row(canvas, _label(layout, "entry_time"), fa_time(content.entry_at))
    if content.kind == "exit":
        if content.exit_at is not None:
            _leader_row(canvas, _label(layout, "exit_time"), fa_time(content.exit_at))
        if content.duration_minutes is not None:
            _leader_row(canvas, _label(layout, "duration"), fa_duration(content.duration_minutes))
        if content.amount is not None:
            _leader_row(canvas, _label(layout, "amount"), fa_money(content.amount))
        if content.method_label:
            _leader_row(canvas, _label(layout, "method"), content.method_label)
    _leader_row(canvas, _label(layout, "ticket_no"), ltr(to_persian_digits(content.ticket_no)))

    if content.kind != "exit" and content.payload:
        _divider(canvas, layout, result)
        art = None
        if layout.show_barcode_art:
            art = load_mono_image(layout.barcode_art_path, WIDTH - 2 * MARGIN, ART_MAX_HEIGHT)
            if art is None and layout.barcode_art_path is not None:
                result.warnings.append("receipt.art_missing")
        _barcode(canvas, content.payload, art, result)
        canvas.text(ltr(to_persian_digits(content.ticket_no)), 30, QFont.Weight.Bold)
        result.sections.append("barcode")
    result.image = canvas.finish()
    return result


def _training_banner(canvas: _Canvas) -> None:
    painter = canvas.painter
    rect = QRectF(MARGIN, canvas.y, WIDTH - 2 * MARGIN, 50)
    painter.fillRect(rect, canvas.ink)
    painter.setPen(QColor(RECEIPT_PAPER))
    painter.setFont(_font(30, QFont.Weight.ExtraBold))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("receipt.training"))
    painter.setPen(canvas.ink)
    canvas.y += 62


def _duplicate_label(canvas: _Canvas, text: str) -> None:
    painter = canvas.painter
    font = _font(46, QFont.Weight.ExtraBold)
    painter.setFont(font)
    width = QFontMetricsF(font).horizontalAdvance(text) + 60
    rect = QRectF((WIDTH - width) / 2, canvas.y, width, 72)
    painter.setPen(QPen(canvas.ink, 4))
    painter.drawRoundedRect(rect, 12, 12)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    canvas.y += 84
