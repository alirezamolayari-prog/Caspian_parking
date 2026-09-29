"""Full-screen blocked-plate alarm (SPEC §4.8) and the status-light table delegate."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget

from caspian_parking.i18n import tr
from caspian_parking.ui.theme.icons import pixmap
from caspian_parking.ui.theme.manager import theme
from caspian_parking.ui.theme.tokens import FontSize, Space
from caspian_parking.ui.widgets.basics import Button, label, qcolor
from caspian_parking.ui.widgets.feedback import LIGHT_TOKENS

ALARM_BEEPS = ((1400, 250), (900, 250), (1400, 250), (900, 400))


def play_alarm() -> None:
    """Audible alarm without blocking the UI (winsound on Windows, a system beep elsewhere)."""
    if sys.platform != "win32":  # pragma: no cover
        QApplication.beep()
        return

    def run() -> None:
        import winsound

        for frequency, duration in ALARM_BEEPS:
            winsound.Beep(frequency, duration)

    threading.Thread(target=run, name="alarm", daemon=True).start()


class AlarmOverlay(QWidget):
    """Red full-window message; Esc / Enter / the button closes it."""

    def __init__(self, parent: QWidget, title: str, message: str, sound: bool = True) -> None:
        window = parent.window()
        super().__init__(window)
        self.setObjectName("Alarm")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setGeometry(window.rect())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XXXL, Space.XXXL, Space.XXXL, Space.XXXL)
        layout.addStretch(1)
        glyph = label()
        glyph.setPixmap(pixmap("octagon-x", "alarm_text", 120))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        self.title = label(title, "display")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.message = label(message, "h2", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message)
        layout.addSpacing(Space.XL)
        self.close_button = Button(tr("alarm.dismiss"), variant="secondary", size="lg", on_click=self.dismiss)
        self.close_button.setMinimumWidth(FontSize.DISPLAY * 8)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        window.installEventFilter(self)
        self.show()
        self.raise_()
        self.setFocus()
        if sound:
            play_alarm()

    def dismiss(self) -> None:
        self.window().removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and isinstance(watched, QWidget):
            self.setGeometry(watched.rect())
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.dismiss()
            return
        super().keyPressEvent(event)


class LightDelegate(QStyledItemDelegate):
    """Paints a status light (green / amber / red / black) centred in a table cell."""

    def __init__(self, light_of: Callable[[Any], str | None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._light_of = light_of

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        super().paint(painter, option, index)
        status = self._light_of(index.data(Qt.ItemDataRole.UserRole))
        if not status:
            return
        palette = theme()
        rect = option.rect  # type: ignore[attr-defined]
        diameter = min(14, rect.height() - 8)
        circle = QRectF(rect.center().x() - diameter / 2, rect.center().y() - diameter / 2, diameter, diameter)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(qcolor(getattr(palette, LIGHT_TOKENS.get(status, "light_black"))))
        ring = palette.light_ring if status == "black" and palette.is_dark else palette.border_strong
        painter.setPen(QPen(qcolor(ring), 1.5))
        painter.drawEllipse(circle)
        painter.restore()
