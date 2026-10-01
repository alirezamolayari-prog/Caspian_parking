"""'Needs review' queue (SPEC §2.2): duplicates entered while gates were offline, sync conflicts."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit

from caspian_parking.data.models import ReviewItem
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_money
from caspian_parking.services.context import AppContext
from caspian_parking.services.review import ReviewError, ReviewRow, ReviewService
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import ask_reason
from caspian_parking.ui.theme.tokens import Size, Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import EmptyState, show_toast
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel


class ReviewScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("review.title"), tr("review.subtitle"))
        self.service = ReviewService(ctx)
        self.item: ReviewItem | None = None
        self._items: list[ReviewItem] = []
        self._rows: list[ReviewRow] = []
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)
        list_card = Card(tr("review.open_items"))
        self.items_model = LazyTableModel(
            [
                Column(tr("review.col_when"), lambda i: fa_datetime(i.created_at_utc), width=150),
                Column(tr("review.col_kind"), lambda i: tr(f"review.kind_{i.kind}"), width=170),
                Column(tr("review.col_summary"), lambda i: i.summary),
            ],
            lambda o, lim: self._items[o : o + lim],
        )
        self.items_table = DataTable(self.items_model)
        self.items_table.clicked.connect(lambda _i: self.show_item(self.items_table.selected_object()))
        list_card.add(self.items_table, 1)
        self.empty = EmptyState("list-checks", tr("review.empty"), tr("review.empty_hint"))
        list_card.add(self.empty, 1)
        row.addWidget(list_card, 2)

        detail = Card()
        self.detail_title = label(tr("review.select"), "h3")
        detail.add(self.detail_title)
        self.detail_hint = label("", "muted", wrap=True)
        detail.add(self.detail_hint)
        self.rows_model = LazyTableModel(
            [
                Column(tr("review.col_node"), lambda r: r.node, width=110),
                Column(tr("review.col_when"), lambda r: fa_datetime(r.when), width=150),
                Column(
                    tr("review.col_amount"), lambda r: fa_money(r.amount) if r.amount is not None else "—", width=140
                ),
                Column(tr("review.col_state"), lambda r: tr("review.cancelled") if r.cancelled else ""),
            ],
            lambda o, lim: self._rows[o : o + lim],
        )
        self.rows_table = DataTable(self.rows_model)
        self.rows_table.clicked.connect(lambda _i: self._show_details())
        detail.add(self.rows_table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(Size.ROW * 4)
        detail.add(self.details)
        actions = QHBoxLayout()
        self.cancel_button = Button(
            tr("review.cancel_selected"), "ban", variant="danger", on_click=self.cancel_selected
        )
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        self.resolution = TextField(tr("review.resolution_hint"))
        actions.addWidget(self.resolution, 2)
        actions.addWidget(Button(tr("review.resolve"), "check", variant="primary", on_click=self.resolve))
        detail.body().addLayout(actions)
        row.addWidget(detail, 3)
        self.show_item(None)
        self.reload()

    def reload(self) -> None:
        self._items = self.service.items()
        self.items_model.reset()
        self.items_table.setVisible(bool(self._items))
        self.empty.setVisible(not self._items)
        if self.item is not None and all(i.id != self.item.id for i in self._items):
            self.show_item(None)

    def show_item(self, item: ReviewItem | None) -> None:
        self.item = item
        self._rows = self.service.rows(item) if item else []
        self.rows_model.reset()
        self.details.setPlainText("")
        self.detail_title.setText(tr(f"review.kind_{item.kind}") if item else tr("review.select"))
        self.detail_hint.setText(tr(f"review.hint_{item.kind}") if item else "")
        self.cancel_button.setVisible(item is not None and item.kind == "duplicate_payment")

    def _show_details(self) -> None:
        row = self.rows_table.selected_object()
        if row is not None:
            self.details.setPlainText(json.dumps(row.details, ensure_ascii=False, indent=1, default=str))

    def cancel_selected(self, reason: str | None = None) -> bool:
        row = self.rows_table.selected_object()
        if self.item is None or row is None:
            show_toast(self, tr("review.select_row"), "info")
            return False
        if reason is None:  # pragma: no cover - dialog
            reason = ask_reason(self, tr("review.cancel_selected"))
            if not reason:
                return False
        try:
            self.service.cancel_payment(self.item, row.id, reason)
        except ReviewError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("review.payment_cancelled"))
        self.show_item(self.item)
        return True

    def resolve(self) -> bool:
        if self.item is None:
            show_toast(self, tr("review.select"), "info")
            return False
        try:
            self.service.resolve(self.item.id, self.resolution.value())
        except ReviewError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.resolution.clear()
        show_toast(self, tr("review.resolved"))
        self.item = None
        self.reload()
        self.show_item(None)
        return True

    def on_show(self) -> None:
        self.reload()
