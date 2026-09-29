"""Common screen scaffold: title row + scrollable content area."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from caspian_parking.services.context import AppContext
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import label


class Screen(QWidget):
    def __init__(self, ctx: AppContext, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.setSpacing(Space.L)
        header = QHBoxLayout()
        header.setSpacing(Space.M)
        titles = QVBoxLayout()
        titles.setSpacing(Space.XXS)
        self.title_label = label(title, "h2")
        titles.addWidget(self.title_label)
        self.subtitle_label = label(subtitle, "muted")
        self.subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(Space.S)
        header.addLayout(self.header_actions)
        root.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(Space.L)
        root.addLayout(self.body, 1)

    def on_show(self) -> None:
        """Called every time the screen becomes visible."""
