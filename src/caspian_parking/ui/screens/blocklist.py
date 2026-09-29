"""Blocklist (SPEC §4.8): register a block per plate, list active blocks, unblock with reason, attempts log."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QTabWidget

from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import plate_from_key
from caspian_parking.data.models import Block
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime
from caspian_parking.services.blocklist import CATEGORIES, GENERIC_CATEGORIES, BlockError, BlocklistService
from caspian_parking.services.context import AppContext
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import ask_reason
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField
from caspian_parking.ui.widgets.feedback import show_toast
from caspian_parking.ui.widgets.plate_input import PlateDelegate, PlateInput
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel


def _plate_of_block(block: object) -> object:
    key = getattr(block, "plate_key", None)
    try:
        return plate_from_key(key) if key else None
    except ValueError:
        return None


class BlocklistScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("block.title"), tr("block.subtitle"))
        self.service = BlocklistService(ctx)
        form = Card(tr("block.register"))
        row = QHBoxLayout()
        row.setSpacing(Space.M)
        self.plate_input = PlateInput()
        row.addWidget(self.plate_input, 2)
        self.category = QComboBox()
        for category in CATEGORIES:
            self.category.addItem(tr(f"block.cat_{category}"), category)
        row.addWidget(self.category)
        self.description = TextField(tr("block.description"))
        row.addWidget(self.description, 2)
        row.addWidget(Button(tr("block.add"), "ban", variant="danger", on_click=self.add))
        form.body().addLayout(row)
        self.body.addWidget(form)
        tabs = QTabWidget()
        details = ctx.can(Permission.VIEW_BLOCK_DETAILS)
        columns = [
            Column(tr("gate.col_plate"), lambda _b: "", width=160),
            Column(tr("block.category"), lambda b: tr(f"block.cat_{b.category}"), width=110),
            Column(
                tr("block.description"),
                lambda b: b.description if details or b.category not in GENERIC_CATEGORIES else tr("block.hidden"),
            ),
            Column(tr("audit.when"), lambda b: fa_datetime(b.created_at_utc), width=150),
        ]
        self.model = LazyTableModel(columns, self.service.active)
        self.table = DataTable(self.model)
        self.table.setItemDelegateForColumn(0, PlateDelegate(_plate_of_block, self.table))
        tabs.addTab(self.table, tr("block.active"))
        attempt_columns = [
            Column(tr("gate.col_plate"), lambda _a: "", width=160),
            Column(tr("audit.when"), lambda a: fa_datetime(a.created_at_utc), width=150),
            Column(tr("block.gate"), lambda a: str(a.gate_code or "")),
        ]
        self.attempts_model = LazyTableModel(attempt_columns, lambda o, lim: self.service.attempts(limit=o + lim)[o:])
        attempts = DataTable(self.attempts_model)
        attempts.setItemDelegateForColumn(0, PlateDelegate(_plate_of_block, attempts))
        tabs.addTab(attempts, tr("block.attempts"))
        self.body.addWidget(tabs, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(Button(tr("block.unblock"), "circle-check", on_click=self.unblock))
        self.body.addLayout(actions)

    def add(self) -> Block | None:
        plate = self.plate_input.plate()
        if plate is None:
            show_toast(self, tr("gate.plate_incomplete"), "warning")
            return None
        if not self.description.value():
            self.description.set_invalid(True)
            return None
        try:
            block = self.service.block_plate(plate, self.category.currentData(), self.description.value())
        except BlockError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        self.plate_input.clear()
        self.description.clear()
        show_toast(self, tr("block.added"))
        self.model.reset()
        return block

    def unblock(self, reason: str | None = None) -> bool:
        block = self.table.selected_object()
        if block is None:
            show_toast(self, tr("gate.select_vehicle"), "info")
            return False
        reason = reason or ask_reason(self, tr("block.unblock"))
        if not reason:
            return False
        try:
            self.service.unblock(block.id, reason)
        except BlockError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.model.reset()
        return True

    def on_show(self) -> None:
        self.model.reset()
        self.attempts_model.reset()
