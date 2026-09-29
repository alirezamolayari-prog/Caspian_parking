"""Hour × weekday heatmap (occupancy report and dashboard)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from caspian_parking.core.digits import to_persian_digits
from caspian_parking.ui.theme.manager import ThemeManager, app_font, theme
from caspian_parking.ui.theme.tokens import FontSize
from caspian_parking.ui.widgets.basics import qcolor

LABEL_WIDTH = 90
HEADER_HEIGHT = 22
CELL_HEIGHT = 26


class HeatmapWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._matrix: list[list[int]] = []
        self._rows: list[str] = []
        self._max = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ThemeManager.instance().changed.connect(self.update)

    def set_data(self, matrix: list[list[int]], rows: list[str], maximum: int | None = None) -> None:
        self._matrix = matrix
        self._rows = rows
        self._max = maximum if maximum is not None else max((max(r) for r in matrix if r), default=0)
        self.updateGeometry()
        self.update()

    def cell_value(self, row: int, hour: int) -> int:
        return self._matrix[row][hour]

    def sizeHint(self) -> QSize:
        return QSize(LABEL_WIDTH + 24 * 28, HEADER_HEIGHT + CELL_HEIGHT * max(1, len(self._matrix)))

    def minimumSizeHint(self) -> QSize:
        return QSize(LABEL_WIDTH + 24 * 14, self.sizeHint().height())

    def paintEvent(self, event: object) -> None:
        palette = theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        font = app_font(FontSize.CAPTION)
        painter.setFont(font)
        width = self.width() - LABEL_WIDTH
        cell_w = width / 24 if width > 0 else 0
        right = self.width()
        muted = qcolor(palette.text_muted)
        # hour header: 0 on the right edge (RTL), 23 on the left
        for hour in range(24):
            x = right - LABEL_WIDTH - (hour + 1) * cell_w
            painter.setPen(muted)
            painter.drawText(
                QRectF(x, 0, cell_w, HEADER_HEIGHT), Qt.AlignmentFlag.AlignCenter, to_persian_digits(str(hour))
            )
        empty = qcolor(palette.surface_alt)
        for row, values in enumerate(self._matrix):
            y = HEADER_HEIGHT + row * CELL_HEIGHT
            painter.setPen(qcolor(palette.text))
            bold = QFont(font)
            bold.setBold(True)
            painter.setFont(bold)
            label = self._rows[row] if row < len(self._rows) else ""
            painter.drawText(
                QRectF(right - LABEL_WIDTH, y, LABEL_WIDTH - 8, CELL_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            painter.setFont(font)
            for hour, value in enumerate(values):
                x = right - LABEL_WIDTH - (hour + 1) * cell_w
                rect = QRectF(x + 1, y + 1, cell_w - 2, CELL_HEIGHT - 2)
                color = empty
                if value and self._max:
                    color = qcolor(palette.accent)
                    color.setAlphaF(0.18 + 0.82 * value / self._max)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 4, 4)
        painter.end()
