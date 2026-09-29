"""Shop accounts and wallets (SPEC §4.6): balance, deposits, members, statement, auto renewal."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout

from caspian_parking.core.jalali import format_jdate
from caspian_parking.data.models import Shop
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_digits, fa_money
from caspian_parking.services.context import AppContext
from caspian_parking.services.exporters import ExportTable, export_excel, export_word
from caspian_parking.services.people import PeopleError, PeopleService
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.alerts import LightDelegate
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, label, set_chip
from caspian_parking.ui.widgets.feedback import show_toast
from caspian_parking.ui.widgets.inputs import MoneyField
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

DEPOSIT_METHODS = ("cash", "card", "mall_card")


class ShopsScreen(Screen):
    model: LazyTableModel

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("shops.title"), tr("shops.subtitle"))
        self.service = PeopleService(ctx)
        self.shop: Shop | None = None
        self.header_actions.addWidget(Button(tr("shops.renew_due"), "refresh-cw", on_click=self.renew_due))
        self.header_actions.addWidget(Button(tr("shops.new"), "store", variant="primary", on_click=self.new))
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)
        list_card = Card()
        self.search = TextField(tr("shops.search"))
        self.search.textChanged.connect(lambda _t: self.model.reset())
        list_card.add(self.search)
        columns = [
            Column("", lambda _s: "", width=40),
            Column(tr("shops.col_name"), lambda s: s.name, width=180),
            Column(tr("shops.col_balance"), lambda s: fa_money(self.service.balance(s.id), False)),
        ]
        self.model = LazyTableModel(columns, lambda o, lim: self.service.shops(self.search.value())[o : o + lim])
        self.table = DataTable(self.model)
        self.table.setItemDelegateForColumn(
            0, LightDelegate(lambda s: ("amber" if self.service.low_balance(s) else "green") if s else None, self.table)
        )
        self.table.clicked.connect(lambda _i: self.show_shop(self.table.selected_object()))
        list_card.add(self.table, 1)
        row.addWidget(list_card, 2)

        detail = Card()
        head = QHBoxLayout()
        self.title = label(tr("shops.new"), "h3")
        head.addWidget(self.title, 1)
        self.balance = chip("", "accent")
        head.addWidget(self.balance)
        detail.body().addLayout(head)
        grid = QGridLayout()
        self.name = TextField(tr("shops.col_name"))
        self.unit = TextField(tr("shops.unit"))
        self.phone = TextField(tr("shops.phone"))
        self.contact = TextField(tr("shops.contact"))
        self.location = QComboBox()
        for value in ("inside", "outside"):
            self.location.addItem(tr(f"subs.location_{value}"), value)
        self.threshold = MoneyField()
        pairs = [
            ("shops.col_name", self.name),
            ("shops.unit", self.unit),
            ("shops.phone", self.phone),
            ("shops.contact", self.contact),
            ("subs.location", self.location),
            ("shops.threshold", self.threshold),
        ]
        for index, (key, widget) in enumerate(pairs):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        detail.body().addLayout(grid)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(Button(tr("common.save"), "check", on_click=self.save))
        detail.body().addLayout(save_row)
        deposit = QHBoxLayout()
        self.deposit_amount = MoneyField()
        self.deposit_method = QComboBox()
        for method in DEPOSIT_METHODS:
            self.deposit_method.addItem(tr(f"payment.{method}"), method)
        deposit.addWidget(label(tr("shops.deposit"), "title"))
        deposit.addWidget(self.deposit_amount, 1)
        deposit.addWidget(self.deposit_method)
        deposit.addWidget(Button(tr("shops.deposit_button"), "wallet", variant="primary", on_click=self.deposit))
        detail.body().addLayout(deposit)
        self.members = label("", "muted", wrap=True)
        detail.add(self.members)
        statement_head = QHBoxLayout()
        statement_head.addWidget(label(tr("shops.statement"), "title"), 1)
        statement_head.addWidget(
            Button(tr("subs.export_excel"), "file-spreadsheet", on_click=lambda: self.export("excel"))
        )
        statement_head.addWidget(Button(tr("subs.export_word"), "file-text", on_click=lambda: self.export("word")))
        detail.body().addLayout(statement_head)
        statement_columns = [
            Column(tr("shops.col_when"), lambda r: fa_datetime(r[0].created_at_utc), width=150),
            Column(tr("shops.col_kind"), lambda r: tr(f"wallet.{r[0].kind}"), width=110),
            Column(tr("shops.col_amount"), lambda r: fa_money(r[0].amount, False), width=120),
            Column(tr("shops.col_running"), lambda r: fa_money(r[1], False)),
        ]
        self._statement: list = []
        self.statement_model = LazyTableModel(statement_columns, lambda o, lim: self._statement[o : o + lim])
        detail.add(DataTable(self.statement_model), 1)
        row.addWidget(detail, 3)
        self.new()

    def new(self) -> None:
        self.show_shop(None)

    def show_shop(self, shop: Shop | None) -> None:
        self.shop = shop
        self.title.setText(shop.name if shop else tr("shops.new"))
        self.name.setText(shop.name if shop else "")
        self.unit.setText(shop.unit or "" if shop else "")
        self.phone.setText(fa_digits(shop.phone or "") if shop else "")
        self.contact.setText(shop.contact_name or "" if shop else "")
        self.location.setCurrentIndex(max(0, self.location.findData(shop.location_type if shop else "inside")))
        self.threshold.set_value(shop.low_balance_threshold if shop else 0)
        self.balance.setVisible(shop is not None)
        self._statement = self.service.statement(shop.id) if shop else []
        self.statement_model.reset()
        if shop is not None:
            balance = self.service.balance(shop.id)
            set_chip(
                self.balance,
                tr("shops.balance", amount=fa_money(balance)),
                "warning" if self.service.low_balance(shop) else "accent",
            )
            names = [p.full_name for p in self.service.members(shop.id)]
            self.members.setText(tr("shops.members", names="، ".join(names) or "—"))
        else:
            self.members.setText("")

    def save(self) -> Shop | None:
        fields = {
            "unit": self.unit.value() or None,
            "phone": self.phone.value() or None,
            "contact_name": self.contact.value() or None,
            "location_type": self.location.currentData(),
            "low_balance_threshold": self.threshold.value(),
        }
        try:
            if self.shop is None:
                shop = self.service.create_shop(self.name.value(), **fields)
            else:
                shop = self.service.update_shop(self.shop.id, name=self.name.value() or self.shop.name, **fields)
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("common.saved"))
        self.model.reset()
        self.show_shop(shop)
        return shop

    def deposit(self) -> bool:
        if self.shop is None:
            return False
        try:
            self.service.deposit(self.shop.id, self.deposit_amount.value(), self.deposit_method.currentData())
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.deposit_amount.set_value(0)
        show_toast(self, tr("shops.deposited"))
        self.model.reset()
        self.show_shop(self.shop)
        return True

    def renew_due(self) -> int:
        try:
            payments = self.service.renew_due_from_wallets()
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return 0
        show_toast(self, tr("shops.renewed", n=fa_digits(len(payments))))
        self.model.reset()
        if self.shop is not None:
            self.show_shop(self.shop)
        return len(payments)

    def export(self, kind: str, open_file: bool = True) -> Path | None:
        if self.shop is None:
            return None
        rows = [
            [fa_datetime(r.created_at_utc), tr(f"wallet.{r.kind}"), r.amount, running] for r, running in self._statement
        ]
        table = ExportTable.simple(
            tr("shops.statement_title", name=self.shop.name),
            [tr("shops.col_when"), tr("shops.col_kind"), tr("shops.col_amount"), tr("shops.col_running")],
            rows,
            widths=[24, 18, 18, 18],
        )
        stamp = format_jdate(self.ctx.clock.now_utc(), sep="-")
        base = self.ctx.data_root.exports / f"{tr('shops.file_name')}_{self.shop.name}_{stamp}"
        path = (
            export_word(table, base.with_suffix(".docx"))
            if kind == "word"
            else export_excel(table, base.with_suffix(".xlsx"))
        )
        if open_file and hasattr(os, "startfile"):  # pragma: no cover
            os.startfile(path)  # type: ignore[attr-defined]
        return path

    def on_show(self) -> None:
        self.model.reset()
