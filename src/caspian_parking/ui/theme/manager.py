"""Theme manager: fonts, palette switching (dark / light / follow Windows) and title-bar color."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
from importlib import resources

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from caspian_parking.ui.theme.qss import build_qss
from caspian_parking.ui.theme.tokens import DARK, FONT_FAMILY, FONT_WEIGHTS, PALETTES, FontSize, Palette

log = logging.getLogger(__name__)

THEME_DARK = "dark"
THEME_LIGHT = "light"
THEME_SYSTEM = "system"
THEME_MODES = (THEME_DARK, THEME_LIGHT, THEME_SYSTEM)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19

_fonts_loaded = False


def load_fonts() -> bool:
    """Register the bundled Vazirmatn weights once. Returns True when the family is available."""
    global _fonts_loaded
    if _fonts_loaded:
        return True
    folder = resources.files("caspian_parking.resources").joinpath("fonts")
    for weight in FONT_WEIGHTS:
        data = folder.joinpath(f"{FONT_FAMILY}-{weight}.ttf").read_bytes()
        if QFontDatabase.addApplicationFontFromData(data) < 0:
            log.warning("could not register font weight %s", weight)
    _fonts_loaded = FONT_FAMILY in QFontDatabase.families()
    return _fonts_loaded


def app_font(pixel_size: int = FontSize.BODY) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    with contextlib.suppress(AttributeError, TypeError):  # older Qt has no font features
        font.setFeature(QFont.Tag("tnum"), 1)  # tabular figures for aligned numbers
    return font


def set_dark_title_bar(widget: QWidget, dark: bool) -> None:
    """Windows 10/11 immersive dark title bar (no-op elsewhere)."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (OSError, AttributeError) as exc:  # pragma: no cover - depends on Windows build
        log.debug("title bar color not applied: %s", exc)


def windows_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def _qpalette(p: Palette) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg,
        QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.surface_sunken,
        QPalette.ColorRole.AlternateBase: p.surface_alt,
        QPalette.ColorRole.Text: p.text,
        QPalette.ColorRole.Button: p.surface_alt,
        QPalette.ColorRole.ButtonText: p.text,
        QPalette.ColorRole.Highlight: p.selection,
        QPalette.ColorRole.HighlightedText: p.text,
        QPalette.ColorRole.ToolTipBase: p.surface_alt,
        QPalette.ColorRole.ToolTipText: p.text,
        QPalette.ColorRole.PlaceholderText: p.text_muted,
        QPalette.ColorRole.Link: p.accent_text,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(p.text_disabled))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(p.text_disabled))
    return pal


class ThemeManager(QObject):
    """Single source of the active palette. Widgets connect to ``changed`` to repaint."""

    changed = Signal(object)
    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self._mode = THEME_DARK
        self._palette: Palette = DARK

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def mode(self) -> str:
        return self._mode

    def resolve(self, mode: str) -> Palette:
        if mode == THEME_SYSTEM:
            return PALETTES[THEME_DARK if windows_prefers_dark() else THEME_LIGHT]
        return PALETTES.get(mode, DARK)

    def apply(self, mode: str) -> Palette:
        if mode not in THEME_MODES:
            mode = THEME_DARK
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            raise RuntimeError("QApplication required")
        if self._mode == THEME_SYSTEM and mode != THEME_SYSTEM:
            _disconnect_scheme(self._on_system_scheme)
        if mode == THEME_SYSTEM and self._mode != THEME_SYSTEM:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme)
        self._mode = mode
        palette = self.resolve(mode)
        self._palette = palette
        load_fonts()
        app.setFont(app_font())
        app.setPalette(_qpalette(palette))
        app.setStyleSheet(build_qss(palette))
        for window in app.topLevelWidgets():
            if window.isWindow() and window.isVisible():
                set_dark_title_bar(window, palette.is_dark)
        self.changed.emit(palette)
        return palette

    def toggle(self) -> Palette:
        return self.apply(THEME_LIGHT if self._palette.is_dark else THEME_DARK)

    def _on_system_scheme(self, *_args: object) -> None:
        if self._mode == THEME_SYSTEM:
            self.apply(THEME_SYSTEM)


def _disconnect_scheme(slot: object) -> None:
    with contextlib.suppress(RuntimeError, TypeError):
        QGuiApplication.styleHints().colorSchemeChanged.disconnect(slot)


def theme() -> Palette:
    """Shortcut used by painting code: the active palette."""
    return ThemeManager.instance().palette
