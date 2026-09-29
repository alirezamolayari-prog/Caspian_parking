"""Specialised inputs: money (integer Rial) and Jalali date picker."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPoint, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.clock import SYSTEM_CLOCK
from caspian_parking.core.digits import (
    PERSIAN_THOUSANDS_SEPARATOR,
    digits_only,
    to_latin_digits,
    to_persian_digits,
)
from caspian_parking.core.jalali import JalaliDate, jalali_month_length, jalali_weekday, local_date, parse_jdate
from caspian_parking.core.money import format_rial
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, jalali_month_name
from caspian_parking.ui.theme.tokens import Size, Space
from caspian_parking.ui.widgets.basics import Button, TextField, label, repolish

MAX_RIAL = 10**15


class TimeField(TextField):
    """``HH:mm`` input with Persian digits.

    QTimeEdit reverses its sections for right-to-left locales (09:30 shows as 30:09), so times use
    this plain LTR field instead. Accepts ``9:30``, ``0930``, ``۹:۳۰`` …; invalid text is flagged.
    """

    time_changed = Signal(object)

    def __init__(self, value: QTime | None = None, parent: QWidget | None = None) -> None:
        super().__init__("۰۰:۰۰", parent=parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMaximumWidth(120)
        self._time = value or QTime(0, 0)
        self.editingFinished.connect(self._parse)
        self._show()

    def time(self) -> QTime:
        return QTime(self._time)

    def setTime(self, value: QTime) -> None:
        changed = value != self._time
        self._time = QTime(value)
        self._show()
        if changed:
            self.time_changed.emit(QTime(value))

    def _show(self) -> None:
        self.setText(to_persian_digits(self._time.toString("HH:mm")))
        self.set_invalid(False)

    def _parse(self) -> None:
        parsed = parse_time_text(self.text())
        if parsed is None:
            self.set_invalid(True)
            return
        self.setTime(parsed)


def parse_time_text(text: str) -> QTime | None:
    digits = digits_only(text)
    if ":" in to_latin_digits(text):
        hh, _, mm = to_latin_digits(text).partition(":")
        hh, mm = digits_only(hh), digits_only(mm)
    elif len(digits) in (3, 4):
        hh, mm = digits[:-2], digits[-2:]
    elif 1 <= len(digits) <= 2:
        hh, mm = digits, "0"
    else:
        return None
    if not hh or not mm:
        return None
    value = QTime(int(hh), int(mm))
    return value if value.isValid() else None


def time_edit(value: QTime | None = None) -> TimeField:
    return TimeField(value)


class MoneyField(QLineEdit):
    """Integer Rial input: accepts any digits, shows Persian digits with thousands separators."""

    value_changed = Signal(int)

    def __init__(self, value: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(Size.INPUT)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._value = 0
        self.textEdited.connect(self._reformat)
        self.set_value(value)

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = max(0, min(int(value), MAX_RIAL))
        self.setText(format_rial(self._value))

    def _reformat(self, text: str) -> None:
        digits = digits_only(text)
        value = min(int(digits), MAX_RIAL) if digits else 0
        # keep the cursor at the same number of digits from the right edge
        tail_digits = len(digits_only(text[self.cursorPosition() :]))
        self._value = value
        formatted = format_rial(value) if digits else ""
        self.setText(formatted)
        position = len(formatted)
        seen = 0
        while position > 0 and seen < tail_digits:
            position -= 1
            if formatted[position] != PERSIAN_THOUSANDS_SEPARATOR:
                seen += 1
        self.setCursorPosition(position)
        self.value_changed.emit(value)


WEEK_HEADER_KEYS = ("wd.sat", "wd.sun", "wd.mon", "wd.tue", "wd.wed", "wd.thu", "wd.fri")
GRID_ROWS = 6


class JalaliCalendarPopup(QFrame):
    """Month grid (Saturday first, right-to-left) for picking a Jalali date."""

    picked = Signal(object)

    def __init__(self, selected: JalaliDate, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Palette")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.selected = selected
        self.year, self.month = selected.year, selected.month
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(Space.S)
        header = QHBoxLayout()
        self.prev_button = Button(icon_name="chevron-right", variant="ghost", size="sm", on_click=self.prev_month)
        self.next_button = Button(icon_name="chevron-left", variant="ghost", size="sm", on_click=self.next_month)
        self.title = label("", "title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.prev_button)
        header.addWidget(self.title, 1)
        header.addWidget(self.next_button)
        layout.addLayout(header)
        self.grid = QGridLayout()
        self.grid.setSpacing(Space.XXS)
        for column, key in enumerate(WEEK_HEADER_KEYS):
            head = label(tr(key), "caption")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(head, 0, column)
        self.day_buttons: list[QPushButton] = []
        for index in range(GRID_ROWS * 7):
            button = QPushButton()
            button.setProperty("size", "sm")
            button.setFixedSize(40, 34)
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, i=index: self._clicked(i))
            self.grid.addWidget(button, 1 + index // 7, index % 7)
            self.day_buttons.append(button)
        layout.addLayout(self.grid)
        self.today_button = Button(tr("date.today"), variant="ghost", size="sm", on_click=self.pick_today)
        layout.addWidget(self.today_button)
        self._render()

    def _first_weekday(self) -> int:
        return jalali_weekday(JalaliDate(self.year, self.month, 1).to_gregorian())

    def _render(self) -> None:
        self.title.setText(f"{jalali_month_name(self.month)} {fa_digits(self.year)}")
        offset = self._first_weekday()
        length = jalali_month_length(self.year, self.month)
        for index, button in enumerate(self.day_buttons):
            day = index - offset + 1
            valid = 1 <= day <= length
            button.setVisible(valid)
            if valid:
                button.setText(to_persian_digits(str(day)))
                button.setChecked(JalaliDate(self.year, self.month, day) == self.selected)
                button.setProperty("variant", "primary" if button.isChecked() else "ghost")
                repolish(button)

    def _clicked(self, index: int) -> None:
        day = index - self._first_weekday() + 1
        self.pick(JalaliDate(self.year, self.month, day))

    def pick(self, value: JalaliDate) -> None:
        self.selected = value
        self.picked.emit(value)
        self.close()

    def pick_today(self) -> None:
        self.pick(JalaliDate.from_gregorian(local_date(SYSTEM_CLOCK.now_utc())))

    def prev_month(self) -> None:
        self.year, self.month = (self.year - 1, 12) if self.month == 1 else (self.year, self.month - 1)
        self._render()

    def next_month(self) -> None:
        self.year, self.month = (self.year + 1, 1) if self.month == 12 else (self.year, self.month + 1)
        self._render()


class JalaliDateEdit(QWidget):
    """Text field (``۱۴۰۵/۰۷/۰۶``, any digits accepted) plus a calendar popup."""

    date_changed = Signal(object)

    def __init__(self, value: JalaliDate | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value or JalaliDate.from_gregorian(local_date(SYSTEM_CLOCK.now_utc()))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        self.field = TextField(tr("date.placeholder"))
        self.field.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.field.editingFinished.connect(self._parse)
        layout.addWidget(self.field, 1)
        self.button = Button(icon_name="calendar-days", variant="ghost", on_click=self.open_popup)
        self.button.setToolTip(tr("date.pick"))
        layout.addWidget(self.button)
        self.popup: JalaliCalendarPopup | None = None
        self._show()

    def value(self) -> JalaliDate:
        return self._value

    def gregorian(self) -> date:
        return self._value.to_gregorian()

    def set_value(self, value: JalaliDate) -> None:
        changed = value != self._value
        self._value = value
        self._show()
        if changed:
            self.date_changed.emit(value)

    def set_gregorian(self, value: date) -> None:
        self.set_value(JalaliDate.from_gregorian(value))

    def shift_days(self, days: int) -> None:
        self.set_gregorian(self.gregorian() + timedelta(days=days))

    def _show(self) -> None:
        self.field.setText(to_persian_digits(self._value.isoformat()))
        self.field.set_invalid(False)

    def _parse(self) -> None:
        try:
            self.set_value(parse_jdate(self.field.text()))
        except ValueError:
            self.field.set_invalid(True)

    def open_popup(self) -> JalaliCalendarPopup:
        self.popup = JalaliCalendarPopup(self._value, self)
        self.popup.picked.connect(self.set_value)
        self.popup.adjustSize()
        point = self.mapToGlobal(QPoint(0, self.height()))
        self.popup.move(point)
        self.popup.show()
        return self.popup
