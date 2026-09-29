"""Free access (SPEC §4.7): staff and mall owner (permanent), guests with date-range permits."""

from __future__ import annotations

from datetime import timedelta

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout

from caspian_parking.core.jalali import local_date
from caspian_parking.data.models import Person
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date
from caspian_parking.services.context import AppContext
from caspian_parking.services.people import PeopleError, PeopleService, PersonInput
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import show_toast
from caspian_parking.ui.widgets.inputs import JalaliDateEdit
from caspian_parking.ui.widgets.plate_input import PlateInput
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

CATEGORIES = ("staff", "owner", "guest")


class FreeAccessScreen(Screen):
    model: LazyTableModel

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("free.title"), tr("free.subtitle"))
        self.service = PeopleService(ctx)
        self.person: Person | None = None
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)
        list_card = Card()
        self.search = TextField(tr("subs.search"))
        self.search.textChanged.connect(lambda _t: self.model.reset())
        list_card.add(self.search)
        columns = [
            Column(tr("subs.col_name"), lambda p: p.full_name, width=180),
            Column(tr("free.category"), lambda p: tr(f"free.cat_{p.free_category}") if p.free_category else ""),
        ]
        self.model = LazyTableModel(columns, lambda o, lim: self.service.search(self.search.value(), "free", o, lim))
        self.table = DataTable(self.model)
        self.table.clicked.connect(lambda _i: self.show_person(self.table.selected_object()))
        list_card.add(self.table, 1)
        row.addWidget(list_card, 2)

        form = Card(tr("free.new"))
        grid = QGridLayout()
        self.first = TextField(tr("subs.first_name"))
        self.last = TextField(tr("subs.last_name"))
        self.mobile = TextField(tr("subs.mobile"))
        self.category = QComboBox()
        for value in CATEGORIES:
            self.category.addItem(tr(f"free.cat_{value}"), value)
        for index, (key, widget) in enumerate(
            (
                ("subs.first_name", self.first),
                ("subs.last_name", self.last),
                ("subs.mobile", self.mobile),
                ("free.category", self.category),
            )
        ):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        form.body().addLayout(grid)
        self.plate_input = PlateInput()
        form.add(label(tr("subs.plates"), "caption"))
        form.add(self.plate_input)
        self.info = label("", "muted", wrap=True)
        form.add(self.info)
        form.add(Button(tr("common.save"), "check", variant="primary", on_click=self.save))
        form.add(label(tr("free.permits"), "title"))
        permit_row = QHBoxLayout()
        today = local_date(ctx.clock.now_utc())
        self.permit_from = JalaliDateEdit()
        self.permit_from.set_gregorian(today)
        self.permit_to = JalaliDateEdit()
        self.permit_to.set_gregorian(today + timedelta(days=1))
        permit_row.addWidget(self.permit_from)
        permit_row.addWidget(self.permit_to)
        permit_row.addWidget(Button(tr("free.add_permit"), "plus", on_click=self.add_permit))
        form.body().addLayout(permit_row)
        self.permits = label("", "muted", wrap=True)
        form.add(self.permits)
        form.body().addStretch(1)
        row.addWidget(form, 3)

    def show_person(self, person: Person | None) -> None:
        self.person = person
        if person is None:
            return
        self.first.setText(person.first_name)
        self.last.setText(person.last_name)
        self.mobile.setText(person.mobile or "")
        self.category.setCurrentIndex(max(0, self.category.findData(person.free_category)))
        plates = self.service.plates_of(person.id)
        self.info.setText(tr("free.info", n=len(plates)))
        permits = self.service.permits_of(person.id)
        self.permits.setText("\n".join(f"{fa_date(p.valid_from)} ← {fa_date(p.valid_to)}" for p in permits) or "—")

    def save(self) -> Person | None:
        plate = self.plate_input.plate()
        data = PersonInput(
            first_name=self.first.value(),
            last_name=self.last.value(),
            mobile=self.mobile.value() or None,
            kind="free",
            free_category=self.category.currentData(),
            plates=[(plate, None)] if plate is not None else [],
        )
        try:
            person = self.service.create_person(data)
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("common.saved"))
        self.model.reset()
        self.show_person(person)
        return person

    def add_permit(self) -> bool:
        if self.person is None:
            return False
        try:
            self.service.add_guest_permit(self.person.id, self.permit_from.gregorian(), self.permit_to.gregorian())
        except PeopleError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.show_person(self.person)
        return True

    def on_show(self) -> None:
        self.model.reset()
