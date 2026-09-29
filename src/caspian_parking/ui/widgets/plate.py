"""Iranian license plate rendering (screen widget + shared painter used by receipts).

Plates are always painted, never shown as plain text, so bidi reordering can never
scramble them (SPEC §3).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QLineF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from caspian_parking.core.digits import to_persian_digits
from caspian_parking.core.plate import Plate, PlateKind
from caspian_parking.i18n import tr
from caspian_parking.ui.theme.tokens import FONT_FAMILY, PLATE, RECEIPT_INK, RECEIPT_PAPER, PlateColors

CAR_ASPECT = 4.7  # 520 x 110 mm
MOTO_ASPECT = 1.6


@dataclass(frozen=True)
class PlateStyle:
    body: str
    ink: str
    frame: str
    strip: str
    strip_ink: str
    empty_ink: str

    @classmethod
    def screen(cls, colors: PlateColors = PLATE) -> PlateStyle:
        return cls(colors.body, colors.ink, colors.frame, colors.strip, colors.strip_ink, colors.empty_ink)

    @classmethod
    def monochrome(cls) -> PlateStyle:
        """Receipt style: pure black on white (thermal printers are 1-bit)."""
        return cls(RECEIPT_PAPER, RECEIPT_INK, RECEIPT_INK, RECEIPT_INK, RECEIPT_PAPER, RECEIPT_INK)


def aspect_for(plate: Plate | None) -> float:
    return MOTO_ASPECT if plate is not None and plate.kind is PlateKind.MOTORCYCLE else CAR_ASPECT


def _font(pixel_size: float, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(max(1, int(pixel_size)))
    font.setWeight(weight)
    return font


def _fit_font(text: str, max_width: float, pixel_size: float, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
    font = _font(pixel_size, weight)
    width = QFontMetricsF(font).horizontalAdvance(text)
    if width > max_width > 0:
        font = _font(pixel_size * max_width / width, weight)
    return font


def _draw_text(painter: QPainter, rect: QRectF, text: str, font: QFont, color: str) -> None:
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def paint_plate(painter: QPainter, rect: QRectF, plate: Plate | None, style: PlateStyle | None = None) -> None:
    """Paint ``plate`` into ``rect``; ``None`` paints an empty frame (receipt without plate)."""
    style = style or PlateStyle.screen()
    painter.save()
    painter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # the plate layout is physical, never mirrored
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    h = rect.height()
    frame_width = max(1.5, h * 0.035)
    radius = h * 0.12
    outline = QPainterPath()
    inner = rect.adjusted(frame_width / 2, frame_width / 2, -frame_width / 2, -frame_width / 2)
    outline.addRoundedRect(inner, radius, radius)
    painter.fillPath(outline, QColor(style.body))
    painter.setClipPath(outline)

    if plate is None:
        _paint_car_layout(painter, inner, None, style, empty=True)
    elif plate.kind is PlateKind.CAR:
        _paint_car_layout(painter, inner, plate, style)
    elif plate.kind is PlateKind.MOTORCYCLE:
        _paint_moto(painter, inner, plate, style)
    else:
        _paint_free(painter, inner, plate, style)

    painter.setClipping(False)
    painter.setPen(QPen(QColor(style.frame), frame_width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(outline)
    painter.restore()


def _paint_car_layout(
    painter: QPainter, rect: QRectF, plate: Plate | None, style: PlateStyle, empty: bool = False
) -> None:
    h = rect.height()
    x, y, w = rect.x(), rect.y(), rect.width()
    strip_w = w * 0.105
    region_w = w * 0.2
    # Left blue strip with I.R. IRAN (always painted: it identifies the frame as a plate).
    strip = QRectF(x, y, strip_w, h)
    painter.fillRect(strip, QColor(style.strip))
    top_text, bottom_text = tr("plate.ir_strip_top"), tr("plate.ir_strip_bottom")
    small = _fit_font(bottom_text, strip_w * 0.8, h * 0.2, QFont.Weight.Bold)
    _draw_text(painter, QRectF(x, y + h * 0.48, strip_w, h * 0.22), top_text, small, style.strip_ink)
    _draw_text(painter, QRectF(x, y + h * 0.7, strip_w, h * 0.22), bottom_text, small, style.strip_ink)
    # Separator before the region box.
    sep_x = x + w - region_w
    painter.setPen(QPen(QColor(style.frame), max(1.0, h * 0.03)))
    painter.drawLine(QLineF(sep_x, y + h * 0.08, sep_x, y + h * 0.92))
    main = QRectF(x + strip_w, y, w - strip_w - region_w, h)
    region_box = QRectF(sep_x, y, region_w, h)
    iran = _fit_font(tr("plate.iran"), region_w * 0.7, h * 0.24, QFont.Weight.DemiBold)
    ink = style.empty_ink if empty else style.ink
    _draw_text(painter, QRectF(region_box.x(), y + h * 0.04, region_w, h * 0.32), tr("plate.iran"), iran, ink)
    if empty or plate is None:
        return
    left, letter, mid, region = plate.parts
    _draw_text(
        painter,
        QRectF(region_box.x(), y + h * 0.32, region_w, h * 0.66),
        to_persian_digits(region),
        _fit_font(to_persian_digits(region), region_w * 0.85, h * 0.56, QFont.Weight.ExtraBold),
        style.ink,
    )
    # Main area: [2 digits][letter][3 digits] left → right, painted in fixed cells.
    cell_left = QRectF(main.x(), y, main.width() * 0.3, h)
    cell_letter = QRectF(main.x() + main.width() * 0.3, y, main.width() * 0.25, h)
    cell_mid = QRectF(main.x() + main.width() * 0.55, y, main.width() * 0.45, h)
    two = _fit_font("۰۰", cell_left.width() * 0.9, h * 0.62)
    three = _fit_font("۰۰۰", cell_mid.width() * 0.9, h * 0.62)
    _draw_text(painter, cell_left, to_persian_digits(left), two, style.ink)
    letter_font = _fit_font(letter, cell_letter.width() * 0.9, h * 0.5, QFont.Weight.ExtraBold)
    _draw_text(painter, cell_letter, letter, letter_font, style.ink)
    _draw_text(painter, cell_mid, to_persian_digits(mid), three, style.ink)


def _paint_moto(painter: QPainter, rect: QRectF, plate: Plate, style: PlateStyle) -> None:
    top, bottom = plate.parts
    h = rect.height()
    top_rect = QRectF(rect.x(), rect.y() + h * 0.04, rect.width(), h * 0.42)
    bottom_rect = QRectF(rect.x(), rect.y() + h * 0.48, rect.width(), h * 0.5)
    painter.setPen(QPen(QColor(style.frame), max(1.0, h * 0.02)))
    line_y = rect.y() + h * 0.47
    painter.drawLine(QLineF(rect.x() + rect.width() * 0.1, line_y, rect.right() - rect.width() * 0.1, line_y))
    _draw_text(painter, top_rect, to_persian_digits(top), _fit_font("۰۰۰", rect.width() * 0.5, h * 0.36), style.ink)
    _draw_text(
        painter, bottom_rect, to_persian_digits(bottom), _fit_font("۰۰۰۰۰", rect.width() * 0.85, h * 0.42), style.ink
    )


def _paint_free(painter: QPainter, rect: QRectF, plate: Plate, style: PlateStyle) -> None:
    text = to_persian_digits(plate.parts[0])
    _draw_text(painter, rect, text, _fit_font(text, rect.width() * 0.9, rect.height() * 0.5), style.ink)


def render_plate_image(plate: Plate | None, height: int = 110, style: PlateStyle | None = None) -> QImage:
    width = round(height * aspect_for(plate))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_plate(painter, QRectF(0, 0, width, height), plate, style)
    painter.end()
    return image


class PlateWidget(QWidget):
    """Big, always-legible plate display (operator screen, lists, dialogs)."""

    def __init__(self, plate: Plate | None = None, height: int = 72, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plate = plate
        self._height = height
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._update_size()

    def plate(self) -> Plate | None:
        return self._plate

    def set_plate(self, plate: Plate | None) -> None:
        self._plate = plate
        self._update_size()
        self.update()

    def set_plate_height(self, height: int) -> None:
        self._height = height
        self._update_size()

    def _update_size(self) -> None:
        self.setFixedSize(self.sizeHint())
        label = self._plate.key if self._plate else tr("plate.empty")
        self.setAccessibleName(label)
        self.setToolTip(to_persian_digits(self._plate.text) if self._plate else tr("plate.empty"))

    def sizeHint(self) -> QSize:
        return QSize(round(self._height * aspect_for(self._plate)), self._height)

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        paint_plate(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), self._plate)
        painter.end()
