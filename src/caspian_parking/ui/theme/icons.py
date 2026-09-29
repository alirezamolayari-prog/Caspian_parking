"""Lucide icons (ISC), recolored from theme tokens at render time."""

from __future__ import annotations

import logging
from functools import cache
from importlib import resources

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from caspian_parking.ui.theme.manager import theme
from caspian_parking.ui.theme.tokens import Size

log = logging.getLogger(__name__)


FALLBACK_ICON = "circle-help"


@cache
def _svg_source(name: str) -> str:
    folder = resources.files("caspian_parking.resources").joinpath("icons")
    path = folder.joinpath(f"{name}.svg")
    if not path.is_file():
        log.warning("missing icon %s, using fallback", name)
        path = folder.joinpath(f"{FALLBACK_ICON}.svg")
    return path.read_text("utf-8")


def icon_exists(name: str) -> bool:
    return resources.files("caspian_parking.resources").joinpath(f"icons/{name}.svg").is_file()


@cache
def _pixmap(name: str, color: str, size: int, ratio: float) -> QPixmap:
    svg = _svg_source(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    physical = max(1, round(size * ratio))
    pixmap = QPixmap(physical, physical)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def pixmap(name: str, color_token: str = "text", size: int = Size.ICON) -> QPixmap:
    color = getattr(theme(), color_token)
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    return _pixmap(name, color, size, ratio)


def icon(name: str, color_token: str = "text", size: int = Size.ICON, active_token: str | None = None) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color_token, size), QIcon.Mode.Normal, QIcon.State.Off)
    if active_token:
        result.addPixmap(pixmap(name, active_token, size), QIcon.Mode.Normal, QIcon.State.On)
    result.addPixmap(pixmap(name, "text_disabled", size), QIcon.Mode.Disabled, QIcon.State.Off)
    return result


def icon_size(size: int = Size.ICON) -> QSize:
    return QSize(size, size)
