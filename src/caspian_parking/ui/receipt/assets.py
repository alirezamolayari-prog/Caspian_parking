"""Built-in receipt artwork written into the data root on first start (the owner can replace it)."""

from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPolygonF

from caspian_parking.ui.theme.tokens import RECEIPT_INK

ART_WIDTH = 540
ART_HEIGHT = 130


def _pine(painter: QPainter, x: float, base: float, height: float, width: float) -> None:
    tiers = 3
    tier_height = height * 0.42
    for tier in range(tiers):
        top = base - height + tier * (height - tier_height) / (tiers - 1) * 0.8
        half = width * (0.55 + 0.45 * (tier + 1) / tiers) / 2
        painter.drawPolygon(
            QPolygonF([QPointF(x, top), QPointF(x + half, top + tier_height), QPointF(x - half, top + tier_height)])
        )
    trunk = width * 0.12
    painter.drawRect(int(x - trunk / 2), int(base - height * 0.16), max(2, int(trunk)), int(height * 0.16))


def draw_forest(width: int = ART_WIDTH, height: int = ART_HEIGHT, seed: int = 1405) -> QImage:
    """Pine-tree forest skyline (default barcode art)."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor(RECEIPT_INK)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    rng = random.Random(seed)
    x = 8.0
    while x < width - 8:
        middle = 1 - abs((x / width) - 0.5) * 1.3  # taller trees in the middle
        tree_height = height * (0.45 + 0.5 * middle * rng.uniform(0.75, 1.0))
        tree_width = tree_height * rng.uniform(0.45, 0.6)
        _pine(painter, x, height, tree_height, tree_width)
        x += tree_width * rng.uniform(0.45, 0.7)
    painter.drawRect(0, height - 4, width, 4)
    painter.end()
    return image


def draw_wave(width: int = ART_WIDTH, height: int = 90) -> QImage:
    """Soft wave skyline (alternative barcode art)."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(QPointF(0, height))
    for x in range(0, width + 1, 4):
        y = height * 0.55 + math.sin(x / width * math.tau * 2) * height * 0.25
        path.lineTo(QPointF(x, y))
    path.lineTo(QPointF(width, height))
    path.closeSubpath()
    painter.fillPath(path, QColor(RECEIPT_INK))
    painter.end()
    return image


def ensure_default_assets(barcode_art_dir: Path) -> list[Path]:
    """Create the built-in art files if they are missing. Returns the files written."""
    barcode_art_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, factory in (("tree.png", draw_forest), ("wave.png", draw_wave)):
        target = barcode_art_dir / name
        if not target.exists():
            factory().save(str(target))
            written.append(target)
    return written
