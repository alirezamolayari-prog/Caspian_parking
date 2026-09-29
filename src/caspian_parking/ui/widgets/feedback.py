"""Feedback widgets: toast, modal dialog with dimmed/blurred backdrop, status light, empty state, alerts."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.i18n import tr
from caspian_parking.ui.theme.icons import pixmap
from caspian_parking.ui.theme.manager import ThemeManager, theme
from caspian_parking.ui.theme.tokens import Motion, Space
from caspian_parking.ui.widgets.basics import Button, label, qcolor, repolish

# ---------------------------------------------------------------- toast

_TOAST_ICONS = {
    "success": ("circle-check", "success_text"),
    "error": ("circle-x", "danger_text"),
    "warning": ("triangle-alert", "warning_text"),
    "info": ("info", "info_text"),
}


class Toast(QFrame):
    """Transient confirmation at the bottom of the window (non-blocking)."""

    def __init__(self, parent: QWidget, text: str, kind: str = "success", duration: int = Motion.TOAST_MS) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.L, Space.M, Space.L, Space.M)
        layout.setSpacing(Space.S)
        icon_name, token = _TOAST_ICONS.get(kind, _TOAST_ICONS["info"])
        glyph = QLabel()
        glyph.setPixmap(pixmap(icon_name, token, 20))
        layout.addWidget(glyph)
        self.text_label = label(text)
        layout.addWidget(self.text_label)
        self.adjustSize()
        self._place()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(Motion.SLOW)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.show()
        self.raise_()
        self._fade.start()
        QTimer.singleShot(duration, self._fade_out)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - Space.XXL
        self.move(QPoint(max(0, x), max(0, y)))

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


def show_toast(parent: QWidget, text: str, kind: str = "success") -> Toast:
    window = parent.window()
    return Toast(window, text, kind)


# ---------------------------------------------------------------- dimmed modal dialogs


class _Backdrop(QWidget):
    """Dim layer painted over the parent window while a dialog is open."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and isinstance(watched, QWidget):
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor(theme().overlay))
        painter.end()


class ModalDialog(QDialog):
    """Themed modal dialog; the parent window is blurred and dimmed while it is open."""

    def __init__(self, parent: QWidget | None, title: str, width: int = 520) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._backdrop: _Backdrop | None = None
        self._blurred: QWidget | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.setSpacing(Space.L)
        self.title_label = label(title, "h3")
        root.addWidget(self.title_label)
        self.content = QVBoxLayout()
        self.content.setSpacing(Space.M)
        root.addLayout(self.content)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(Space.S)
        self.buttons.addStretch(1)
        root.addLayout(self.buttons)

    def add_button(
        self, text: str, variant: str | None = None, role: str = "accept", icon_name: str | None = None
    ) -> Button:
        button = Button(text, icon_name=icon_name, variant=variant)
        if role == "accept":
            button.clicked.connect(self.accept)
            button.setDefault(True)
        elif role == "reject":
            button.clicked.connect(self.reject)
        self.buttons.addWidget(button)
        return button

    def showEvent(self, event: object) -> None:
        parent = self.parentWidget()
        parent_window = parent.window() if parent is not None else None
        if parent_window is not None and self._backdrop is None:
            central = parent_window.centralWidget() if isinstance(parent_window, QMainWindow) else None
            if central is not None and central.graphicsEffect() is None:
                blur = QGraphicsBlurEffect(central)
                blur.setBlurRadius(6)
                central.setGraphicsEffect(blur)
                self._blurred = central
            self._backdrop = _Backdrop(parent_window)
            self._backdrop.show()
            self._backdrop.raise_()
        super().showEvent(event)  # type: ignore[arg-type]

    def done(self, result: int) -> None:
        self._clear_backdrop()
        super().done(result)

    def _clear_backdrop(self) -> None:
        if self._backdrop is not None:
            self._backdrop.deleteLater()
            self._backdrop = None
        if self._blurred is not None:
            self._blurred.setGraphicsEffect(None)  # type: ignore[arg-type]
            self._blurred = None


def confirm(parent: QWidget, title: str, message: str, ok_text: str | None = None, danger: bool = False) -> bool:
    dialog = ModalDialog(parent, title, width=460)
    dialog.content.addWidget(label(message, wrap=True))
    dialog.add_button(tr("common.cancel"), role="reject")
    dialog.add_button(ok_text or tr("common.ok"), variant="danger" if danger else "primary")
    return dialog.exec() == QDialog.DialogCode.Accepted


# ---------------------------------------------------------------- status light

LIGHT_TOKENS = {"green": "light_green", "amber": "light_amber", "red": "light_red", "black": "light_black"}


class StatusLight(QWidget):
    """Subscription/contract light. Black gets a visible ring in dark mode (SPEC §3)."""

    def __init__(self, status: str = "green", diameter: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = status
        self._diameter = diameter
        self.setFixedSize(QSize(diameter + 4, diameter + 4))
        ThemeManager.instance().changed.connect(self.update)
        self._update_tooltip()

    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status
        self._update_tooltip()
        self.update()

    def _update_tooltip(self) -> None:
        self.setToolTip(tr(f"light.{self._status}"))
        self.setAccessibleName(tr(f"light.{self._status}"))

    def paintEvent(self, event: object) -> None:
        palette = theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, self._diameter, self._diameter)
        painter.setBrush(qcolor(getattr(palette, LIGHT_TOKENS.get(self._status, "light_black"))))
        ring_needed = self._status == "black" and palette.is_dark
        painter.setPen(QPen(qcolor(palette.light_ring), 1.5) if ring_needed else QPen(qcolor(palette.border_strong), 1))
        painter.drawEllipse(rect)
        painter.end()


# ---------------------------------------------------------------- empty state


class EmptyState(QWidget):
    """Designed empty/error state — never a blank area."""

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str = "",
        action: Button | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.XXL, Space.XL, Space.XXL)
        layout.setSpacing(Space.S)
        layout.addStretch(1)
        self._icon_name = icon_name
        self.glyph = QLabel()
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.glyph)
        self.title_label = label(title, "title")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        self.subtitle_label = label(subtitle, "muted", wrap=True)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setVisible(bool(subtitle))
        layout.addWidget(self.subtitle_label)
        if action is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(action)
            row.addStretch(1)
            layout.addLayout(row)
        layout.addStretch(1)
        self._refresh()
        ThemeManager.instance().changed.connect(self._refresh)

    def _refresh(self, *_args: object) -> None:
        self.glyph.setPixmap(pixmap(self._icon_name, "text_muted", 48))


# ---------------------------------------------------------------- alert bar (SPEC §4.15)


@dataclass(frozen=True)
class Alert:
    key: str
    text: str
    level: str = "warning"  # info | warning | danger


class AlertBar(QWidget):
    """Non-blocking bar listing active hardware/system alerts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._alerts: dict[str, Alert] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(Space.L, Space.S, Space.L, 0)
        self._layout.setSpacing(Space.XS)
        self.setVisible(False)

    def alerts(self) -> list[Alert]:
        return list(self._alerts.values())

    def raise_alert(self, alert: Alert) -> None:
        self._alerts[alert.key] = alert
        self._rebuild()

    def clear_alert(self, key: str) -> None:
        if self._alerts.pop(key, None) is not None:
            self._rebuild()

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for alert in self._alerts.values():
            frame = QFrame()
            frame.setProperty("banner", alert.level)
            row = QHBoxLayout(frame)
            row.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
            glyph = QLabel()
            token = {"danger": "danger_text", "info": "info_text"}.get(alert.level, "warning_text")
            glyph.setPixmap(pixmap("triangle-alert", token, 18))
            row.addWidget(glyph)
            row.addWidget(label(alert.text), 1)
            close = Button(icon_name="x", variant="ghost", size="sm")
            close.setToolTip(tr("common.dismiss"))
            close.clicked.connect(lambda _c=False, k=alert.key: self.clear_alert(k))
            row.addWidget(close)
            repolish(frame)
            self._layout.addWidget(frame)
        self.setVisible(bool(self._alerts))
