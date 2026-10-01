"""RFID cards of one person (SPEC §6): list, add (type or read the number), mark lost (with reason)."""

from __future__ import annotations

import functools

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from caspian_parking.i18n import tr
from caspian_parking.i18n.bidi import ltr
from caspian_parking.services.context import AppContext
from caspian_parking.services.hardware import CardService, HardwareError
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, TextField, chip, label
from caspian_parking.ui.widgets.feedback import show_toast


class CardsBox(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.service = CardService(ctx)
        self.person_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        layout.addWidget(label(tr("card.title"), "title"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        add = QHBoxLayout()
        self.uid = TextField(tr("card.uid_hint"), persian_digits=False)
        self.uid.returnPressed.connect(self.add_card)
        add.addWidget(self.uid, 1)
        add.addWidget(Button(tr("card.add"), "key-round", on_click=self.add_card))
        layout.addLayout(add)

    def set_person(self, person_id: str | None) -> None:
        self.person_id = person_id
        self.uid.setEnabled(person_id is not None)
        self.reload()

    def reload(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if self.person_id is None:
            return
        for card in self.service.cards_of(self.person_id):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(label(ltr(card.uid)))
            line.addWidget(
                chip(
                    tr("card.active") if card.is_active else tr("card.lost"), "success" if card.is_active else "danger"
                )
            )
            line.addStretch(1)
            if card.is_active:
                line.addWidget(
                    Button(
                        tr("card.mark_lost"),
                        "ban",
                        variant="ghost",
                        on_click=functools.partial(self.mark_lost, card.id),
                    )
                )
            self.rows.addWidget(row)

    def add_card(self) -> bool:
        if self.person_id is None:
            return False
        try:
            self.service.assign(self.person_id, self.uid.text())
        except HardwareError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.uid.clear()
        self.reload()
        show_toast(self, tr("card.added"))
        return True

    def mark_lost(self, card_id: str, reason: str | None = None) -> bool:
        if reason is None:  # pragma: no cover - dialog
            from caspian_parking.ui.screens.gate_dialogs import ask_reason

            reason = ask_reason(self, tr("card.mark_lost"), tr("card.lost_hint"))
            if not reason:
                return False
        try:
            self.service.deactivate(card_id, reason)
        except HardwareError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.reload()
        return True
