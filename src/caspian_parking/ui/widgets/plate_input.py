"""Plate entry: four boxes in the physical plate order (12 | ب | 345 | ایران 22), or 3 + 5 for motorcycles."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from caspian_parking.core.digits import digits_only
from caspian_parking.core.plate import LETTERS, Plate, PlateKind, car_plate, motorcycle_plate, parse_plate
from caspian_parking.i18n import tr
from caspian_parking.ui.theme.manager import app_font
from caspian_parking.ui.theme.tokens import FontSize, Size, Space
from caspian_parking.ui.widgets.basics import TextField, label
from caspian_parking.ui.widgets.plate import aspect_for, paint_plate

FIELD_WIDTHS = {2: 76, 3: 100, 5: 150}


class _DigitBox(TextField):
    def __init__(self, length: int) -> None:
        super().__init__("۰" * length)
        self.length = length
        self.setMaxLength(length)
        self.setProperty("scale", "lg")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(FIELD_WIDTHS[length])
        self.setMinimumHeight(Size.BUTTON_LG)
        font = app_font(FontSize.H2)
        font.setBold(True)
        self.setFont(font)

    def digits(self) -> str:
        return digits_only(self.text())


class PlateInput(QWidget):
    """Emits ``plate_changed`` (Plate or None) on every edit and ``submitted`` on Enter in the last box."""

    plate_changed = Signal(object)
    submitted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # physical plate order
        self._mode = PlateKind.CAR
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        self.left = _DigitBox(2)
        self.letter = QComboBox()
        self.letter.addItems(list(LETTERS))
        self.letter.setMinimumHeight(Size.BUTTON_LG)
        self.letter.setFixedWidth(96)
        self.letter.setProperty("scale", "lg")
        letter_font = app_font(FontSize.H2)
        letter_font.setBold(True)
        self.letter.setFont(letter_font)
        self.mid = _DigitBox(3)
        self.iran = label(tr("plate.iran"), "muted")
        self.region = _DigitBox(2)
        self.top = _DigitBox(3)
        self.bottom = _DigitBox(5)
        for widget in (self.left, self.letter, self.mid, self.iran, self.region, self.top, self.bottom):
            layout.addWidget(widget)
        layout.addStretch(1)
        self._order_car: list[QWidget] = [self.left, self.letter, self.mid, self.region]
        self._order_moto: list[QWidget] = [self.top, self.bottom]
        for box in (self.left, self.mid, self.region, self.top, self.bottom):
            box.textEdited.connect(lambda _t, b=box: self._edited(b))
            box.installEventFilter(self)
        self.letter.currentIndexChanged.connect(lambda _i: self._letter_chosen())
        self.letter.installEventFilter(self)
        self.set_mode(PlateKind.CAR)

    # ---------------------------------------------------------------- mode
    def mode(self) -> PlateKind:
        return self._mode

    def set_mode(self, mode: PlateKind) -> None:
        self._mode = mode if mode is PlateKind.MOTORCYCLE else PlateKind.CAR
        car = self._mode is PlateKind.CAR
        for widget in [*self._order_car, self.iran]:
            widget.setVisible(car)
        for widget in self._order_moto:
            widget.setVisible(not car)
        self.plate_changed.emit(self.plate())

    def _order(self) -> list[QWidget]:
        return self._order_car if self._mode is PlateKind.CAR else self._order_moto

    # ---------------------------------------------------------------- values
    def plate(self) -> Plate | None:
        try:
            if self._mode is PlateKind.CAR:
                return car_plate(self.left.digits(), self.letter.currentText(), self.mid.digits(), self.region.digits())
            return motorcycle_plate(self.top.digits(), self.bottom.digits())
        except ValueError:
            return None

    def is_empty(self) -> bool:
        boxes = (self.left, self.mid, self.region) if self._mode is PlateKind.CAR else (self.top, self.bottom)
        return not any(box.digits() for box in boxes)

    def set_plate(self, plate: Plate | None) -> None:
        for box in (self.left, self.mid, self.region, self.top, self.bottom):
            box.clear()
        if plate is not None and plate.kind is PlateKind.MOTORCYCLE:
            self.set_mode(PlateKind.MOTORCYCLE)
            self.top.setText(plate.parts[0])
            self.bottom.setText(plate.parts[1])
            self.top.textEdited.emit(self.top.text())
            self.bottom.textEdited.emit(self.bottom.text())
        elif plate is not None and plate.kind is PlateKind.CAR:
            self.set_mode(PlateKind.CAR)
            left, letter, mid, region = plate.parts
            self.left.setText(left)
            self.letter.setCurrentText(letter)
            self.mid.setText(mid)
            self.region.setText(region)
            for box in (self.left, self.mid, self.region):
                box.textEdited.emit(box.text())
        self.plate_changed.emit(self.plate())

    def set_text(self, text: str) -> bool:
        """Accept a whole plate typed or pasted in one go."""
        try:
            plate = parse_plate(text, allow_free=False)
        except ValueError:
            return False
        self.set_plate(plate)
        return True

    def clear(self) -> None:
        for box in (self.left, self.mid, self.region, self.top, self.bottom):
            box.clear()
        self.letter.setCurrentIndex(self.letter.findText("ب"))
        self.plate_changed.emit(None)

    def focus_first(self) -> None:
        self._order()[0].setFocus()

    # ---------------------------------------------------------------- navigation
    def _edited(self, box: _DigitBox) -> None:
        self.plate_changed.emit(self.plate())
        if len(box.digits()) >= box.length:
            self._advance(box)

    def _letter_chosen(self) -> None:
        self.plate_changed.emit(self.plate())
        if self.letter.hasFocus():
            self.mid.setFocus()

    def _advance(self, widget: QWidget) -> None:
        order = self._order()
        index = order.index(widget) if widget in order else -1
        if 0 <= index < len(order) - 1:
            order[index + 1].setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.submitted.emit()
                return True
            if event.key() == Qt.Key.Key_Backspace and isinstance(watched, _DigitBox) and not watched.text():
                order = self._order()
                index = order.index(watched) if watched in order else 0
                if index > 0:
                    order[index - 1].setFocus()
                    return True
        return super().eventFilter(watched, event)


class PlateDelegate(QStyledItemDelegate):
    """Paints a plate in a table cell (plates are never shown as plain text; SPEC §3)."""

    def __init__(self, plate_of: object, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._plate_of = plate_of

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        super().paint(painter, option, index)  # background / selection
        row_object = index.data(Qt.ItemDataRole.UserRole)
        plate = self._plate_of(row_object)  # type: ignore[operator]
        rect: QRect = option.rect.adjusted(4, 4, -4, -4)  # type: ignore[attr-defined]
        height = rect.height()
        width = min(rect.width(), round(height * aspect_for(plate)))
        target = QRect(rect.right() - width, rect.top(), width, height)
        paint_plate(painter, target.toRectF(), plate)
