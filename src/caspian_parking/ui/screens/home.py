"""Home screen: greeting, site overview (levels & capacities) and quick links."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from caspian_parking.data.repositories.system import LevelRepository
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_long_date, fa_number
from caspian_parking.services.context import AppContext
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Card, chip, label


class HomeScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("home.title"))
        self.greeting = Card(raised=True)
        self.hello = label("", "h3")
        self.today = label("", "muted")
        self.greeting.add(self.hello)
        self.greeting.add(self.today)
        self.body.addWidget(self.greeting)
        self.levels_card = Card(tr("home.levels"))
        self.levels_box: QWidget | None = None
        self.body.addWidget(self.levels_card)
        self.body.addStretch(1)
        self.on_show()

    def on_show(self) -> None:
        name = self.ctx.user.display_name if self.ctx.user else ""
        self.hello.setText(tr("home.hello", name=name))
        self.today.setText(fa_long_date(self.ctx.clock.now_utc()))
        if self.levels_box is not None:
            self.levels_box.setParent(None)  # detach now; deleteLater alone is deferred
            self.levels_box.deleteLater()
        self.levels_box = QWidget()
        grid = QGridLayout(self.levels_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.S)
        self.levels_card.add(self.levels_box)
        with self.ctx.read() as session:
            levels = list(LevelRepository(session).active())
        for row, level in enumerate(levels):
            grid.addWidget(label(level.name, "title"), row, 0)
            if level.is_parking:
                capacity = label(tr("home.capacity", n=fa_number(level.capacity)), "muted")
                state = chip(tr("level.open"), "success") if level.is_open else chip(tr("level.closed"), "neutral")
            else:
                capacity = label(tr("level.not_parking"), "muted")
                state = chip(tr("level.storage"), "neutral")
            grid.addWidget(capacity, row, 1)
            box = QHBoxLayout()
            box.addWidget(state)
            box.addStretch(1)
            grid.addLayout(box, row, 2)
        grid.setColumnStretch(2, 1)
