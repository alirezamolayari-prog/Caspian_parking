"""Small building blocks styled through QSS properties (no colors here)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.digits import ARABIC_DIGITS, PERSIAN_DIGITS, normalize_input
from caspian_parking.ui.theme.icons import icon, icon_size
from caspian_parking.ui.theme.manager import ThemeManager, theme
from caspian_parking.ui.theme.tokens import Motion, Size, Space


def repolish(widget: QWidget) -> None:
    """Re-apply QSS after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def chip(text: str, kind: str = "neutral") -> QLabel:
    widget = QLabel(text)
    widget.setProperty("chip", kind)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setFixedHeight(Size.CHIP)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return widget


def set_chip(widget: QLabel, text: str, kind: str) -> None:
    widget.setText(text)
    widget.setProperty("chip", kind)
    repolish(widget)


class Button(QPushButton):
    """Themed push button with an optional Lucide icon.

    variant: primary | secondary | danger | ghost | (default); size: lg | sm | (default)
    """

    def __init__(
        self,
        text: str = "",
        icon_name: str | None = None,
        variant: str | None = None,
        size: str | None = None,
        on_click: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        if variant:
            self.setProperty("variant", variant)
        if size:
            self.setProperty("scale", size)
        if size == "lg":
            self.setMinimumHeight(Size.BUTTON_LG)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_icon()
        ThemeManager.instance().changed.connect(self._refresh_icon)
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())

    def _icon_token(self) -> str:
        variant = self.property("variant")
        if variant in ("primary", "danger"):
            return "text_on_accent"
        if variant == "secondary":
            return "secondary_text"
        return "text"

    def _refresh_icon(self, *_args: object) -> None:
        if self._icon_name:
            size = Size.ICON_LG if self.property("scale") == "lg" else Size.ICON
            self.setIcon(icon(self._icon_name, self._icon_token(), size))
            self.setIconSize(icon_size(size))


class Card(QFrame):
    """Rounded surface with optional title row and soft shadow."""

    def __init__(
        self, title: str | None = None, raised: bool = False, shadow: bool | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", "raised" if raised else "true")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        self._layout.setSpacing(Space.M)
        self.title_label: QLabel | None = None
        if title:
            self.title_label = label(title, "title")
            self._layout.addWidget(self.title_label)
        if shadow if shadow is not None else raised:  # shadows only on raised cards (cheap to paint)
            self._shadow = QGraphicsDropShadowEffect(self)
            self._shadow.setBlurRadius(24)
            self._shadow.setOffset(0, 4)
            self._apply_shadow()
            self.setGraphicsEffect(self._shadow)
            ThemeManager.instance().changed.connect(self._apply_shadow)

    def _apply_shadow(self, *_args: object) -> None:
        self._shadow.setColor(qcolor(theme().shadow))

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget


def _rgba(css: str) -> int:
    """Parse a CSS-style RGBA token (four comma-separated channels) into a QRgb integer."""
    inner = css[css.index("(") + 1 : css.rindex(")")]
    r, g, b, a = (part.strip() for part in inner.split(","))
    return QColor(int(r), int(g), int(b), round(float(a) * 255)).rgba()


def qcolor(token_value: str) -> QColor:
    """Token string (hex or RGBA form) → QColor."""
    if token_value.startswith("rgba"):
        return QColor.fromRgba(_rgba(token_value))
    return QColor(token_value)


def divider() -> QFrame:
    line = QFrame()
    line.setProperty("divider", "true")
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def hbox(
    *widgets: QWidget | None, spacing: int = Space.S, margins: int = 0, stretch_at: int | None = None
) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(margins, margins, margins, margins)
    for index, widget in enumerate(widgets):
        if stretch_at == index:
            layout.addStretch(1)
        if widget is not None:
            layout.addWidget(widget)
    if stretch_at is not None and stretch_at >= len(widgets):
        layout.addStretch(1)
    return layout


class TextField(QLineEdit):
    """Line edit that shows Persian digits/letters for anything typed and returns normalized values."""

    _DISPLAY = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", **dict(zip(ARABIC_DIGITS, PERSIAN_DIGITS, strict=True))})

    def __init__(self, placeholder: str = "", persian_digits: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._persian_digits = persian_digits
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(Size.INPUT)
        self.textEdited.connect(self._normalize_display)

    def _normalize_display(self, text: str) -> None:
        display = text.translate(self._DISPLAY)
        if self._persian_digits:
            display = display.translate(str.maketrans("0123456789", PERSIAN_DIGITS))
        if display != text:
            pos = self.cursorPosition()
            self.setText(display)
            self.setCursorPosition(pos)

    def value(self) -> str:
        """Normalized value: Latin digits, Persian letter forms, trimmed."""
        return normalize_input(self.text())

    def set_invalid(self, invalid: bool) -> None:
        self.setProperty("invalid", "true" if invalid else "false")
        repolish(self)


def fade_in(widget: QWidget, duration: int = Motion.NORMAL) -> QPropertyAnimation:
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))  # type: ignore[arg-type]
    animation.start()
    return animation
