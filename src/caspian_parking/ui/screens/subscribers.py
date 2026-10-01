"""Subscribers (SPEC §4.6): list with status lights, profile, plates, payments, negative subscription,
follow-up list with Excel/Word export, and Excel import."""

from __future__ import annotations

import functools
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.jalali import format_jdate
from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import PlateKind, plate_from_key
from caspian_parking.core.subscriptions import light_for
from caspian_parking.data.models import Person
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date, fa_digits, fa_money
from caspian_parking.services.context import AppContext
from caspian_parking.services.follow_up import export_follow_up, follow_up_rows
from caspian_parking.services.people import PeopleError, PeopleService, PersonInput
from caspian_parking.services.subscriber_import import import_subscribers, write_template
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import ask_reason
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.alerts import LightDelegate
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, label, set_chip
from caspian_parking.ui.widgets.cards_box import CardsBox
from caspian_parking.ui.widgets.feedback import EmptyState, ModalDialog, StatusLight, show_toast
from caspian_parking.ui.widgets.inputs import MoneyField
from caspian_parking.ui.widgets.plate import PlateWidget
from caspian_parking.ui.widgets.plate_input import PlateInput
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

METHODS = ("cash", "card", "mall_card", "wallet")
LIGHT_CHIP = {"green": "success", "amber": "warning", "red": "danger", "black": "neutral"}


class PayDialog(ModalDialog):
    """Payment with a preview of the new end date (and the negative days that will be deducted)."""

    def __init__(self, parent: QWidget, service: PeopleService, person: Person) -> None:
        super().__init__(parent, tr("subs.pay_title", name=person.full_name), width=520)
        self.service = service
        self.person = person
        preview = service.preview_payment(person.id)
        self.amount = MoneyField(preview.amount)
        self.method = QComboBox()
        for method in METHODS:
            if method == "wallet" and not person.shop_id:
                continue
            self.method.addItem(tr(f"payment.{method}"), method)
        grid = QGridLayout()
        grid.addWidget(label(tr("subs.amount"), "caption"), 0, 0)
        grid.addWidget(self.amount, 1, 0)
        grid.addWidget(label(tr("subs.method"), "caption"), 0, 1)
        grid.addWidget(self.method, 1, 1)
        self.content.addLayout(grid)
        renewal = preview.renewal
        self.content.addWidget(label(tr("subs.new_end", date=fa_date(renewal.new_end)), "title"))
        if renewal.early:
            self.content.addWidget(label(tr("subs.early_note"), "muted", wrap=True))
        if renewal.used_days:
            days = "، ".join(fa_date(d) for d in renewal.used_days)
            self.content.addWidget(
                label(tr("subs.negative_note", n=fa_digits(len(renewal.used_days)), days=days), "danger", wrap=True)
            )
        self.add_button(tr("common.cancel"), role="reject")
        self.add_button(tr("subs.confirm_pay"), variant="primary", icon_name="check")

    def pay(self) -> bool:
        try:
            self.service.pay_subscription(self.person.id, self.method.currentData(), self.amount.value())
        except PeopleError as exc:
            show_toast(self.parentWidget() or self, tr(str(exc)), "error")
            return False
        return True


class SubscriberProfile(QWidget):
    def __init__(self, screen: SubscribersScreen) -> None:
        super().__init__()
        self.owner = screen
        self.ctx = screen.ctx
        self.service = screen.service
        self.person: Person | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.M)
        head = QHBoxLayout()
        self.light = StatusLight("black", 18)
        head.addWidget(self.light)
        self.name = label("", "h3")
        head.addWidget(self.name, 1)
        self.days = chip("", "neutral")
        head.addWidget(self.days)
        layout.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.M)
        grid.setVerticalSpacing(Space.XS)
        self.first = TextField(tr("subs.first_name"))
        self.last = TextField(tr("subs.last_name"))
        self.mobile = TextField(tr("subs.mobile"))
        self.brand = TextField(tr("subs.brand"))
        self.model = TextField(tr("subs.vehicle_model"))
        self.shop = QComboBox()
        self.location = QComboBox()
        for value in ("inside", "outside"):
            self.location.addItem(tr(f"subs.location_{value}"), value)
        self.payer = QComboBox()
        for value in ("self", "shop"):
            self.payer.addItem(tr(f"subs.payer_{value}"), value)
        self.price = MoneyField()
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 9)
        self.notes = TextField(tr("subs.notes"))
        fields = [
            ("subs.first_name", self.first),
            ("subs.last_name", self.last),
            ("subs.mobile", self.mobile),
            ("subs.vehicle_model", self.model),
            ("subs.shop", self.shop),
            ("subs.brand", self.brand),
            ("subs.location", self.location),
            ("subs.payer", self.payer),
            ("subs.price_override", self.price),
            ("subs.max_concurrent", self.concurrent),
        ]
        for index, (key, widget) in enumerate(fields):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        layout.addLayout(grid)
        layout.addWidget(self.notes)
        layout.addWidget(label(tr("subs.plates"), "title"))
        self.plates_box = QVBoxLayout()
        layout.addLayout(self.plates_box)
        self.plate_input = PlateInput()
        layout.addWidget(self.plate_input)
        add_row = QHBoxLayout()  # on its own line so the profile never needs a horizontal scroll bar
        self.moto = QCheckBox(tr("vehicle.motorcycle"))
        self.moto.toggled.connect(lambda on: self.plate_input.set_mode(PlateKind.MOTORCYCLE if on else PlateKind.CAR))
        add_row.addWidget(self.moto)
        add_row.addStretch(1)
        add_row.addWidget(Button(tr("subs.add_plate"), "plus", on_click=self.add_plate))
        layout.addLayout(add_row)
        self.cards = CardsBox(self.ctx)
        layout.addWidget(self.cards)
        self.subscription = label("", "muted", wrap=True)
        layout.addWidget(self.subscription)
        actions = QHBoxLayout()
        self.pay_button = Button(tr("subs.pay"), "banknote", variant="primary", on_click=self.pay)
        self.negative_button = Button(tr("subs.allow_negative"), "clock", on_click=self.allow_negative)
        self.exempt_button = Button(tr("subs.night_exempt"), "moon-star", on_click=self.toggle_exempt)
        self.save_button = Button(tr("common.save"), "check", on_click=self.save)
        for button in (self.pay_button, self.negative_button, self.exempt_button, self.save_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        self.negative_button.setVisible(self.ctx.can(Permission.ALLOW_NEGATIVE_SUBSCRIPTION))
        self.exempt_button.setVisible(self.ctx.can(Permission.ADJUST_NIGHT_FINES))

    def reload_shops(self) -> None:
        current = self.shop.currentData()
        self.shop.clear()
        self.shop.addItem(tr("subs.no_shop"), None)
        for shop in self.service.shops():
            self.shop.addItem(shop.name, shop.id)
        self.shop.setCurrentIndex(max(0, self.shop.findData(current)))

    def show_person(self, person: Person | None) -> None:
        self.reload_shops()
        self.person = person
        editing = person is not None
        for widget in (self.pay_button, self.negative_button, self.exempt_button):
            widget.setEnabled(editing)
        self.plate_input.clear()
        self.cards.set_person(person.id if person is not None else None)
        while self.plates_box.count():
            item = self.plates_box.takeAt(0)
            old = item.widget() if item is not None else None
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        if person is None:
            self.name.setText(tr("subs.new"))
            for field in (self.first, self.last, self.mobile, self.brand, self.model, self.notes):
                field.clear()
            self.price.set_value(0)
            self.concurrent.setValue(1)
            self.subscription.setText("")
            self.light.set_status("black")
            set_chip(self.days, "", "neutral")
            self.days.setVisible(False)
            self.first.setFocus()
            return
        state = self.service.state(person)
        self.light.set_status(state.light.value)
        self.name.setText(person.full_name)
        self.days.setVisible(True)
        set_chip(self.days, tr("subs.days_left", n=fa_digits(state.days_left)), LIGHT_CHIP[state.light.value])
        self.first.setText(person.first_name)
        self.last.setText(person.last_name)
        self.mobile.setText(fa_digits(person.mobile or ""))
        self.brand.setText(person.brand or "")
        self.model.setText(person.vehicle_model or "")
        self.notes.setText(person.notes or "")
        self.shop.setCurrentIndex(max(0, self.shop.findData(person.shop_id)))
        self.location.setCurrentIndex(max(0, self.location.findData(person.location_type)))
        self.payer.setCurrentIndex(max(0, self.payer.findData(person.payer)))
        self.price.set_value(person.price_override or 0)
        self.concurrent.setValue(person.max_concurrent)
        for plate in self.service.plates_of(person.id):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            try:
                row_layout.addWidget(PlateWidget(plate_from_key(plate.plate_key), height=40))
            except ValueError:
                row_layout.addWidget(label(plate.plate_key))
            if plate.description:
                row_layout.addWidget(label(plate.description, "muted"))
            row_layout.addStretch(1)
            row_layout.addWidget(
                Button(icon_name="x", variant="ghost", on_click=functools.partial(self.remove_plate, plate.id))
            )
            self.plates_box.addWidget(row)
        end = fa_date(person.subscription_end_utc) if person.subscription_end_utc else "—"
        parts = [tr("subs.end_date", date=end)]
        if person.negative_allowed:
            parts.append(tr("subs.negative_active", n=fa_digits(person.negative_max_days)))
        if person.night_exempt:
            parts.append(tr("subs.exempt_active"))
        self.subscription.setText(" — ".join(parts))

    def collect(self) -> PersonInput:
        return PersonInput(
            first_name=self.first.value(),
            last_name=self.last.value(),
            mobile=self.mobile.value() or None,
            shop_id=self.shop.currentData(),
            brand=self.brand.value() or None,
            location_type=self.location.currentData(),
            vehicle_model=self.model.value() or None,
            notes=self.notes.value() or None,
            payer=self.payer.currentData(),
            price_override=self.price.value() or None,
            max_concurrent=self.concurrent.value(),
        )

    def save(self) -> Person | None:
        data = self.collect()
        try:
            if self.person is None:
                plate = self.plate_input.plate()
                data.plates = [(plate, None)] if plate is not None else []
                person = self.service.create_person(data)
            else:
                person = self.service.update_person(
                    self.person.id,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    mobile=data.mobile,
                    shop_id=data.shop_id,
                    brand=data.brand,
                    location_type=data.location_type,
                    vehicle_model=data.vehicle_model,
                    notes=data.notes,
                    payer=data.payer,
                    price_override=data.price_override,
                    max_concurrent=data.max_concurrent,
                )
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("common.saved"))
        self.owner.reload(select_id=person.id)
        return person

    def add_plate(self) -> bool:
        plate = self.plate_input.plate()
        if plate is None:
            show_toast(self, tr("gate.plate_incomplete"), "warning")
            return False
        if self.person is None:
            return self.save() is not None
        try:
            self.service.add_plate(self.person.id, plate)
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.show_person(self.service.get(self.person.id))
        return True

    def remove_plate(self, plate_row_id: str, reason: str | None = None) -> bool:
        reason = reason or ask_reason(self, tr("subs.remove_plate"))
        if not reason or self.person is None:
            return False
        self.service.remove_plate(plate_row_id, reason)
        self.show_person(self.service.get(self.person.id))
        return True

    def pay(self, auto_accept: bool = False) -> bool:
        if self.person is None:
            return False
        dialog = PayDialog(self, self.service, self.person)
        if not auto_accept and dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if not dialog.pay():
            return False
        show_toast(self, tr("subs.paid"))
        self.owner.reload(select_id=self.person.id)
        return True

    def allow_negative(self, reason: str | None = None) -> bool:
        if self.person is None:
            return False
        reason = reason or ask_reason(self, tr("subs.allow_negative"), tr("subs.allow_negative_hint"))
        if not reason:
            return False
        try:
            self.service.allow_negative(self.person.id, self.person.negative_max_days or 10, reason)
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.owner.reload(select_id=self.person.id)
        return True

    def toggle_exempt(self, reason: str | None = None) -> bool:
        if self.person is None:
            return False
        reason = reason or ask_reason(self, tr("subs.night_exempt"))
        if not reason:
            return False
        self.service.set_night_exempt(self.person.id, not self.person.night_exempt, reason)
        self.owner.reload(select_id=self.person.id)
        return True


class SubscribersScreen(Screen):
    model: LazyTableModel
    profile: SubscriberProfile

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("subs.title"), tr("subs.subtitle"))
        self.service = PeopleService(ctx)
        self.header_actions.addWidget(Button(tr("subs.new"), "user-plus", variant="primary", on_click=self.new))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._list_tab(), tr("subs.tab_list"))
        self.tabs.addTab(self._follow_tab(), tr("subs.tab_follow"))
        self.tabs.addTab(self._import_tab(), tr("subs.tab_import"))
        self.body.addWidget(self.tabs, 1)
        self.reload()

    # ---------------------------------------------------------------- list
    def _list_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, Space.M, 0, 0)
        row.setSpacing(Space.L)
        list_card = Card()
        self.search = TextField(tr("subs.search"))
        self.search.textChanged.connect(lambda _t: self.reload())
        list_card.add(self.search)
        now = self.ctx.clock.now_utc
        columns = [
            Column("", lambda _p: "", width=40),
            Column(tr("subs.col_name"), lambda p: p.full_name, width=170),
            Column(tr("subs.col_brand"), lambda p: p.brand or "", width=140),
            Column(tr("subs.col_end"), lambda p: fa_date(p.subscription_end_utc) if p.subscription_end_utc else "—"),
        ]
        self.model = LazyTableModel(
            columns, lambda o, lim: self.service.search(self.search.value(), "subscriber", o, lim)
        )
        self.table = DataTable(self.model)
        self.table.setItemDelegateForColumn(
            0, LightDelegate(lambda p: light_for(p.subscription_end_utc, now()).value if p else None, self.table)
        )
        self.table.clicked.connect(lambda _i: self.profile.show_person(self.table.selected_object()))
        list_card.add(self.table, 1)
        row.addWidget(list_card, 2)
        profile_card = Card()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.profile = SubscriberProfile(self)
        scroll.setWidget(self.profile)
        profile_card.add(scroll, 1)
        row.addWidget(profile_card, 3)
        return page

    def new(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.profile.show_person(None)

    def reload(self, select_id: str | None = None) -> None:
        self.model.reset()
        if select_id is not None:
            self.profile.show_person(self.service.get(select_id))
        elif self.profile.person is None:
            self.profile.show_person(None)
        self._reload_follow()

    def on_show(self) -> None:
        self.reload()

    # ---------------------------------------------------------------- follow-up
    def _follow_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, Space.M, 0, 0)
        buttons = QHBoxLayout()
        self.follow_summary = label("", "muted")
        buttons.addWidget(self.follow_summary, 1)
        buttons.addWidget(Button(tr("subs.export_excel"), "file-spreadsheet", on_click=lambda: self.export("excel")))
        buttons.addWidget(Button(tr("subs.export_word"), "file-text", on_click=lambda: self.export("word")))
        layout.addLayout(buttons)
        columns = [
            Column("", lambda _r: "", width=40),
            Column(tr("follow.col_name"), lambda r: r.person.full_name, width=170),
            Column(tr("follow.col_shop"), lambda r: r.shop_name, width=140),
            Column(tr("follow.col_phone"), lambda r: fa_digits(r.person.mobile or ""), width=120),
            Column(tr("follow.col_end"), lambda r: fa_digits(format_jdate(r.end)) if r.end else "—", width=110),
            Column(tr("follow.col_days"), lambda r: fa_digits(r.days_left), width=90),
            Column(tr("follow.col_due"), lambda r: fa_money(r.amount_due, False)),
        ]
        self._follow_rows: list = []
        self.follow_model = LazyTableModel(columns, lambda o, lim: self._follow_rows[o : o + lim])
        self.follow_table = DataTable(self.follow_model)
        self.follow_table.setItemDelegateForColumn(
            0, LightDelegate(lambda r: r.light.value if r else None, self.follow_table)
        )
        layout.addWidget(self.follow_table, 1)
        self.follow_empty = EmptyState("circle-check", tr("subs.follow_empty"), tr("subs.follow_empty_hint"))
        layout.addWidget(self.follow_empty, 1)
        return page

    def _reload_follow(self) -> None:
        self._follow_rows = follow_up_rows(self.ctx)
        self.follow_model.reset()
        has_rows = bool(self._follow_rows)
        self.follow_table.setVisible(has_rows)
        self.follow_empty.setVisible(not has_rows)
        self.follow_summary.setText(tr("subs.follow_summary", n=fa_digits(len(self._follow_rows))))

    def export(self, kind: str, open_file: bool = True) -> Path:
        path = export_follow_up(self.ctx, self.ctx.data_root.exports, kind)
        show_toast(self, tr("subs.exported", path=path.name))
        if open_file and hasattr(os, "startfile"):  # pragma: no cover - opens Excel/Word
            os.startfile(path)  # type: ignore[attr-defined]
        return path

    # ---------------------------------------------------------------- import
    def _import_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, Space.M, 0, 0)
        card = Card(tr("subs.import_title"))
        card.add(label(tr("subs.import_hint"), "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(Button(tr("subs.save_template"), "download", on_click=self.save_template))
        row.addWidget(Button(tr("subs.choose_file"), "upload", variant="primary", on_click=self.choose_import))
        row.addStretch(1)
        card.body().addLayout(row)
        self.import_result = label("", "title", wrap=True)
        card.add(self.import_result)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def save_template(self) -> Path:
        path = write_template(self.ctx.data_root.exports / "subscribers_import_template.xlsx")
        show_toast(self, tr("subs.exported", path=path.name))
        return path

    def choose_import(self) -> None:  # pragma: no cover - file dialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("subs.choose_file"), str(self.ctx.data_root.exports), "Excel (*.xlsx)"
        )
        if path:
            self.run_import(Path(path))

    def run_import(self, path: Path) -> None:
        try:
            report = import_subscribers(self.ctx, path)
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return
        text = tr(
            "subs.import_done",
            total=fa_digits(report.total_rows),
            ok=fa_digits(report.imported),
            bad=fa_digits(report.skipped_rows),
        )
        if report.report_path is not None:
            text += "\n" + tr("subs.import_report", path=str(report.report_path))
        self.import_result.setText(text)
        self.reload()
