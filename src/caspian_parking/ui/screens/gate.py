"""Main operator screen (SPEC §4.2–4.4): entry lane, exit lane, vehicles inside, occupancy, counters.

Keyboard first: Enter/Space prints the entry receipt, F-keys trigger the main actions, a
keyboard-wedge scanner can scan a ticket at any time (it goes straight to the exit lane).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import Plate, PlateKind
from caspian_parking.core.tariff import PassThroughType, VehicleType, VisitKind
from caspian_parking.data.models import ActiveSession, EntryEvent
from caspian_parking.devices.printer import PrinterError
from caspian_parking.devices.scanner import ScannerSource, SerialScanner, WedgeScanner
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, fa_duration, fa_ltr, fa_money, fa_number, fa_time
from caspian_parking.services.context import AppContext
from caspian_parking.services.gate_service import (
    AlreadyInside,
    EntryResult,
    ExitQuote,
    GateError,
    GateService,
    OpenDebt,
    PaymentMethod,
)
from caspian_parking.services.receipts import entry_content, exit_content, plate_of
from caspian_parking.services.settings import get_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.receipt.renderer import InsideRow, render_inside_list
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import (
    AlreadyInsideDialog,
    LostTicketDialog,
    ReceiptPreviewDialog,
    ask_reason,
)
from caspian_parking.ui.shell.main_window import add_shortcut_row
from caspian_parking.ui.theme.tokens import Size, Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, label, repolish, set_chip
from caspian_parking.ui.widgets.feedback import Alert, EmptyState, confirm, show_toast
from caspian_parking.ui.widgets.inputs import MoneyField
from caspian_parking.ui.widgets.plate import PlateWidget
from caspian_parking.ui.widgets.plate_input import PlateDelegate, PlateInput
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

log = logging.getLogger(__name__)

REFRESH_MS = 30_000
SIDE_PANEL_MIN_WIDTH = 380
VEHICLE_CHOICES = (VehicleType.SEDAN, VehicleType.VAN, VehicleType.TRUCK, VehicleType.OTHER)

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Enter / Space", "gate.print_entry"),
    ("F2", "gate.no_plate"),
    ("F3", "gate.motorcycle"),
    ("F4", "gate.pass_through"),
    ("F5", "gate.scan_ticket"),
    ("F6", "gate.calculate"),
    ("F7", "payment.cash"),
    ("F8", "payment.card"),
    ("F9", "payment.mall_card"),
    ("F10", "gate.flee"),
    ("F11", "gate.night"),
    ("F12", "gate.lost_ticket"),
)


class GateScreen(Screen):
    inside_table: DataTable
    inside_model: LazyTableModel
    today_model: LazyTableModel

    def __init__(self, ctx: AppContext, printing: ReceiptPrinting | None = None) -> None:
        super().__init__(ctx, tr("gate.title"))
        self.gate = GateService(ctx)
        self.printing = printing or ReceiptPrinting(ctx)
        self.quote: ExitQuote | None = None
        self.lost_ticket = False
        self.pending_debts: list[OpenDebt] = []
        self.vehicle = VehicleType.SEDAN
        self.scanner = self._create_scanner()
        self.subtitle_label.setVisible(True)

        top = QHBoxLayout()
        top.setSpacing(Space.L)
        top.addWidget(self._entry_card(), 5)
        top.addWidget(self._exit_card(), 5)
        top.addWidget(self._side_panel(), 4)
        self.body.addLayout(top, 1)
        self.body.addWidget(self._status_strip())
        self._shortcuts()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_lists)
        self._timer.start(REFRESH_MS)
        self.refresh_lists()
        self.reset_exit()

    def _create_scanner(self) -> ScannerSource | None:
        devices = self.ctx.config.devices
        scanner: ScannerSource | None = None
        if devices.scanner_mode == "wedge":
            scanner = WedgeScanner(parent=self)
        elif devices.scanner_mode == "serial" and devices.scanner_port:
            scanner = SerialScanner(devices.scanner_port, devices.scanner_baud, parent=self)
        if scanner is not None:
            scanner.scanned.connect(self.on_scanned)
        return scanner

    # ================================================================ entry lane
    def _entry_card(self) -> QWidget:
        card = Card(tr("gate.entry_lane"), raised=True)
        self.entry_preview = PlateWidget(None, height=84)
        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        preview_row.addWidget(self.entry_preview)
        preview_row.addStretch(1)
        card.body().addLayout(preview_row)
        self.plate_input = PlateInput()
        self.plate_input.plate_changed.connect(self._plate_changed)
        self.plate_input.submitted.connect(self.print_entry)
        card.add(self.plate_input)
        types = QHBoxLayout()
        types.setSpacing(Space.XS)
        self.vehicle_group = QButtonGroup(self)
        self.vehicle_buttons: dict[VehicleType, Button] = {}
        for vehicle in VEHICLE_CHOICES:
            button = Button(tr(f"vehicle.{vehicle.value}"), size="sm")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, v=vehicle: self.set_vehicle(v))
            self.vehicle_group.addButton(button)
            self.vehicle_buttons[vehicle] = button
            types.addWidget(button)
        types.addStretch(1)
        card.body().addLayout(types)
        self.entry_banner = QFrame()
        banner_row = QHBoxLayout(self.entry_banner)
        banner_row.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        self.entry_banner_text = label("", wrap=True)
        banner_row.addWidget(self.entry_banner_text, 1)
        self.collect_cash = Button(
            tr("payment.cash"), size="sm", on_click=lambda: self.collect_debts(PaymentMethod.CASH)
        )
        self.collect_card = Button(
            tr("payment.card"), size="sm", on_click=lambda: self.collect_debts(PaymentMethod.CARD)
        )
        banner_row.addWidget(self.collect_cash)
        banner_row.addWidget(self.collect_card)
        self.entry_banner.setVisible(False)
        card.add(self.entry_banner)
        self.print_button = Button(
            tr("gate.print_entry"), "printer", variant="primary", size="lg", on_click=self.print_entry
        )
        card.add(self.print_button)
        actions = QGridLayout()
        actions.setSpacing(Space.S)
        self.no_plate_button = Button(tr("gate.no_plate"), "ticket", on_click=self.print_without_plate)
        self.moto_button = Button(tr("gate.motorcycle"), "bike", on_click=self.toggle_motorcycle)
        self.moto_button.setCheckable(True)
        self.pass_button = Button(tr("gate.pass_through"), "arrow-right-left", on_click=self.open_pass_menu)
        self.lost_button = Button(tr("gate.lost_ticket"), "search", on_click=self.lost_ticket_flow)
        for index, button in enumerate((self.no_plate_button, self.moto_button, self.pass_button, self.lost_button)):
            button.setMinimumHeight(Size.TOUCH_MIN)
            actions.addWidget(button, index // 2, index % 2)
        card.body().addLayout(actions)
        card.body().addStretch(1)
        self.set_vehicle(VehicleType.SEDAN)
        return card

    def set_vehicle(self, vehicle: VehicleType) -> None:
        self.vehicle = vehicle
        if vehicle in self.vehicle_buttons:
            self.vehicle_buttons[vehicle].setChecked(True)

    def toggle_motorcycle(self) -> None:
        moto = self.plate_input.mode() is not PlateKind.MOTORCYCLE
        self.plate_input.set_mode(PlateKind.MOTORCYCLE if moto else PlateKind.CAR)
        self.moto_button.setChecked(moto)
        for button in self.vehicle_buttons.values():
            button.setEnabled(not moto)
        self.plate_input.focus_first()

    def current_vehicle(self) -> VehicleType:
        return VehicleType.MOTORCYCLE if self.plate_input.mode() is PlateKind.MOTORCYCLE else self.vehicle

    def _plate_changed(self, plate: Plate | None) -> None:
        self.entry_preview.set_plate(plate)
        self.pending_debts = self.gate.open_debts(plate) if plate is not None else []
        self._show_debt_banner()

    def _show_debt_banner(self) -> None:
        total = sum(d.remaining for d in self.pending_debts)
        visible = total > 0
        self.entry_banner.setVisible(visible)
        if visible:
            self.entry_banner.setProperty("banner", "danger")
            repolish(self.entry_banner)
            self.entry_banner_text.setText(tr("gate.debt_banner", amount=fa_money(total)))

    def collect_debts(self, method: PaymentMethod) -> bool:
        if not self.pending_debts:
            return False
        try:
            for debt in self.pending_debts:
                self.gate.collect_debt(debt.debt.id, method)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("gate.debt_collected"))
        plate = self.plate_input.plate()
        self.pending_debts = self.gate.open_debts(plate) if plate else []
        self._show_debt_banner()
        return True

    def print_entry(self) -> EntryResult | None:
        plate = self.plate_input.plate()
        if plate is None:
            show_toast(self, tr("gate.plate_incomplete"), "warning")
            self.plate_input.focus_first()
            return None
        return self._register(plate, self.current_vehicle(), VisitKind.TRANSIENT)

    def print_without_plate(self) -> EntryResult | None:
        return self._register(None, self.current_vehicle(), VisitKind.TRANSIENT)

    def open_pass_menu(self) -> QMenu:
        menu = QMenu(self)
        for pass_type in PassThroughType:
            menu.addAction(tr(f"pass.{pass_type.value}"), functools.partial(self.print_pass_through, pass_type))
        menu.popup(self.pass_button.mapToGlobal(self.pass_button.rect().bottomLeft()))
        return menu

    def print_pass_through(self, pass_type: PassThroughType) -> EntryResult | None:
        plate = self.plate_input.plate()
        if plate is None and not self.plate_input.is_empty():
            show_toast(self, tr("gate.plate_incomplete"), "warning")
            return None
        vehicle = VehicleType.MOTORCYCLE if pass_type is PassThroughType.COURIER else self.current_vehicle()
        if pass_type is PassThroughType.VAN_UNLOADING:
            vehicle = VehicleType.VAN
        return self._register(plate, vehicle, VisitKind.PASS_THROUGH, pass_type.value)

    def _register(
        self, plate: Plate | None, vehicle: VehicleType, kind: VisitKind, pass_type: str | None = None
    ) -> EntryResult | None:
        try:
            result = self.gate.register_entry(plate, vehicle, kind, pass_type)
        except AlreadyInside as exc:
            self._already_inside(exc.session)
            return None
        except GateError as exc:
            show_toast(self, tr(str(exc)), "warning")
            return None
        content = entry_content(result.session, result.payload, training=self.ctx.training)
        self._print(content, "entry", preview=self._preview_enabled())
        show_toast(self, tr("gate.entry_done", ticket=fa_ltr(str(result.ticket))))
        self.plate_input.clear()
        if self.plate_input.mode() is PlateKind.MOTORCYCLE:
            self.toggle_motorcycle()
        self.set_vehicle(VehicleType.SEDAN)
        self.plate_input.focus_first()
        self.refresh_lists()
        return result

    def _preview_enabled(self) -> bool:
        with self.ctx.read() as session:
            return bool(get_setting(session, "gate.preview_before_print"))

    def _print(self, content: object, job: str, preview: bool = False) -> bool:
        from caspian_parking.core.receipt import ReceiptContent

        assert isinstance(content, ReceiptContent)
        result = self.printing.render(content)
        for warning in result.warnings:
            self._alert("receipt", tr(warning), "warning")
        if preview:
            dialog = ReceiptPreviewDialog(self, QPixmap.fromImage(result.image))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
        try:
            self.printing.print_result(result, job)
        except PrinterError as exc:
            self._alert("printer", tr(str(exc)), "danger")
            show_toast(self, tr(str(exc)), "error")
            return False
        self._clear_alert("printer")
        return True

    def _already_inside(self, session: ActiveSession) -> None:
        dialog = AlreadyInsideDialog(self, session)
        code = dialog.exec()
        if code == AlreadyInsideDialog.EXIT:
            self.load_exit(session)
        elif code == AlreadyInsideDialog.DUPLICATE:
            self.print_duplicate(session)

    def print_duplicate(self, session: ActiveSession) -> bool:
        try:
            active = self.gate.reprint_duplicate(session.id)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        from caspian_parking.core.barcode import encode_payload

        payload = encode_payload(active.gate_code, active.ticket_sequence, active.entry_at_utc, self.gate.hmac_key())
        printed = self._print(entry_content(active, payload, duplicate=True, training=self.ctx.training), "duplicate")
        if printed:
            show_toast(self, tr("gate.duplicate_done"))
        self.refresh_lists()
        return printed

    # ================================================================ exit lane
    def _exit_card(self) -> QWidget:
        card = Card(tr("gate.exit_lane"), raised=True)
        search = QHBoxLayout()
        self.ticket_field = TextField(tr("gate.ticket_placeholder"))
        self.ticket_field.setProperty("scale", "lg")
        self.ticket_field.returnPressed.connect(self.calculate)
        search.addWidget(self.ticket_field, 1)
        search.addWidget(Button(tr("gate.calculate"), "calculator", on_click=self.calculate))
        card.body().addLayout(search)
        self.exit_empty = EmptyState("scan-barcode", tr("gate.exit_empty"), tr("gate.exit_empty_hint"))
        card.add(self.exit_empty, 1)
        self.exit_details = QWidget()
        details = QVBoxLayout(self.exit_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(Space.S)
        head = QHBoxLayout()
        self.exit_plate = PlateWidget(None, height=56)
        head.addWidget(self.exit_plate)
        head.addStretch(1)
        self.exit_flags = chip("", "warning")
        head.addWidget(self.exit_flags)
        details.addLayout(head)
        self.exit_info = label("", "muted", wrap=True)
        details.addWidget(self.exit_info)
        self.exit_lines = QVBoxLayout()
        self.exit_lines.setSpacing(Space.XXS)
        details.addLayout(self.exit_lines)
        self.exit_total = label("", "kpi")
        details.addWidget(self.exit_total)
        self.adjust_box = QWidget()
        adjust = QGridLayout(self.adjust_box)
        adjust.setContentsMargins(0, 0, 0, 0)
        self.manual_amount = MoneyField()
        self.night_amount = MoneyField()
        self.adjust_reason = TextField(tr("gate.reason_placeholder"))
        adjust.addWidget(label(tr("gate.manual_amount"), "caption"), 0, 0)
        adjust.addWidget(self.manual_amount, 1, 0)
        adjust.addWidget(label(tr("gate.night_amount"), "caption"), 0, 1)
        adjust.addWidget(self.night_amount, 1, 1)
        adjust.addWidget(self.adjust_reason, 2, 0, 1, 2)
        details.addWidget(self.adjust_box)
        pay = QHBoxLayout()
        pay.setSpacing(Space.S)
        self.cash_button = Button(
            tr("payment.cash"), "banknote", variant="primary", size="lg", on_click=lambda: self.pay(PaymentMethod.CASH)
        )
        self.card_button = Button(
            tr("payment.card"),
            "credit-card",
            variant="primary",
            size="lg",
            on_click=lambda: self.pay(PaymentMethod.CARD),
        )
        self.mall_button = Button(
            tr("payment.mall_card_short"), "building-2", size="lg", on_click=lambda: self.pay(PaymentMethod.MALL_CARD)
        )
        for button in (self.cash_button, self.card_button, self.mall_button):
            pay.addWidget(button)
        details.addLayout(pay)
        more = QHBoxLayout()
        self.exit_receipt = QCheckBox(tr("gate.exit_receipt"))
        more.addWidget(self.exit_receipt)
        more.addStretch(1)
        self.night_button = Button(tr("gate.night"), "moon-star", variant="ghost", on_click=self.mark_night_current)
        self.cancel_button = Button(tr("gate.cancel_entry"), "ban", variant="ghost", on_click=self.cancel_current)
        self.flee_button = Button(tr("gate.flee"), "siren", variant="danger", on_click=self.flee_current)
        for button in (self.night_button, self.cancel_button, self.flee_button):
            more.addWidget(button)
        details.addLayout(more)
        card.add(self.exit_details, 1)
        return card

    def reset_exit(self) -> None:
        self.quote = None
        self.lost_ticket = False
        self.exit_details.setVisible(False)
        self.exit_empty.setVisible(True)
        self.ticket_field.clear()
        self.manual_amount.set_value(0)
        self.night_amount.set_value(0)
        self.adjust_reason.clear()
        self.adjust_box.setVisible(
            self.ctx.can(Permission.ADJUST_AMOUNTS) or self.ctx.can(Permission.ADJUST_NIGHT_FINES)
        )
        self.cancel_button.setVisible(self.ctx.can(Permission.CANCEL_TRANSACTIONS))
        with self.ctx.read() as session:
            self.exit_receipt.setChecked(bool(get_setting(session, "receipt.print_exit_receipt")))

    def on_scanned(self, text: str) -> None:
        if not self.isVisible():
            return
        self.ticket_field.setText(text)
        self.calculate()

    def calculate(self) -> ExitQuote | None:
        text = self.ticket_field.value()
        if not text:
            self.ticket_field.setFocus()
            return None
        try:
            session = self.gate.resolve(text)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "warning")
            self.ticket_field.selectAll()
            return None
        return self.load_exit(session)

    def load_exit(self, session: ActiveSession, lost: bool = False) -> ExitQuote | None:
        try:
            self.quote = self.gate.quote(session.id, lost_ticket=lost)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "warning")
            return None
        self.lost_ticket = lost
        quote = self.quote
        self.exit_empty.setVisible(False)
        self.exit_details.setVisible(True)
        self.exit_plate.set_plate(plate_of(session))
        info = tr(
            "gate.exit_info",
            entry=fa_time(session.entry_at_utc),
            exit=fa_time(quote.exit_at),
            duration=fa_duration(quote.breakdown.total_minutes),
            ticket=fa_ltr(session.ticket_no),
        )
        self.exit_info.setText(info)
        while self.exit_lines.count():
            item = self.exit_lines.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for line in quote.breakdown.lines():
            text = tr(f"price.{line.key}")
            if line.key == "extra_minutes":
                text = tr("price.extra_minutes_n", n=fa_digits(line.quantity))
            elif line.key == "night_fines":
                text = tr("price.night_fines_n", n=fa_digits(line.quantity))
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label(text), 1)
            row.addWidget(label(fa_money(line.amount)))
            self.exit_lines.addWidget(holder)
        self.exit_total.setText(tr("price.total", amount=fa_money(quote.amount_due)))
        flags = [tr(f"flag.{f}") for f in sorted(quote.breakdown.flags)] + ([tr("gate.lost_flag")] if lost else [])
        self.exit_flags.setVisible(bool(flags))
        set_chip(self.exit_flags, "، ".join(flags), "warning")
        self.manual_amount.set_value(quote.amount_due)
        self.night_amount.set_value(quote.breakdown.night_fines)
        self.night_amount.setEnabled(self.ctx.can(Permission.ADJUST_NIGHT_FINES) and quote.breakdown.nights > 0)
        self.manual_amount.setEnabled(self.ctx.can(Permission.ADJUST_AMOUNTS))
        self.cash_button.setFocus()
        return quote

    def pay(self, method: PaymentMethod) -> bool:
        if self.quote is None:
            show_toast(self, tr("gate.no_exit_selected"), "info")
            return False
        quote = self.quote
        manual = self.manual_amount.value() if self.manual_amount.isEnabled() else None
        night = self.night_amount.value() if self.night_amount.isEnabled() else None
        if night is not None and night == quote.breakdown.night_fines:
            night = None
        if manual is not None:
            expected = quote.breakdown.transient_fee + (night if night is not None else quote.breakdown.night_fines)
            if manual == expected:
                manual = None
        try:
            visit = self.gate.complete_exit(
                quote,
                method if quote.amount_due or manual else None,
                manual_amount=manual,
                night_fines=night,
                reason=self.adjust_reason.value() or None,
            )
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        if self.exit_receipt.isChecked():
            content = exit_content(
                quote.session,
                quote.exit_at,
                quote.breakdown.total_minutes,
                visit.amount_paid,
                method.value if visit.amount_paid else None,
                training=self.ctx.training,
            )
            self._print(content, "exit")
        show_toast(self, tr("gate.exit_done", amount=fa_money(visit.amount_paid)))
        self.reset_exit()
        self.refresh_lists()
        self.plate_input.focus_first()
        return True

    def flee_current(self) -> bool:
        if self.quote is None:
            return False
        if not confirm(
            self, tr("gate.flee_title"), tr("gate.flee_body", amount=fa_money(self.quote.amount_due)), danger=True
        ):
            return False
        return self.flee(self.quote)

    def flee(self, quote: ExitQuote) -> bool:
        try:
            self.gate.flee(quote)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("gate.flee_done"), "warning")
        self.reset_exit()
        self.refresh_lists()
        return True

    def cancel_current(self, reason: str | None = None) -> bool:
        if self.quote is None:
            return False
        reason = reason or ask_reason(self, tr("gate.cancel_entry"), tr("gate.cancel_hint"))
        if not reason:
            return False
        try:
            self.gate.cancel_entry(self.quote.session.id, reason)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("gate.cancelled"))
        self.reset_exit()
        self.refresh_lists()
        return True

    def mark_night_current(self) -> bool:
        session = self.quote.session if self.quote is not None else self.inside_table.selected_object()
        if session is None:
            show_toast(self, tr("gate.select_vehicle"), "info")
            return False
        try:
            self.gate.mark_night(session.id)
        except GateError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("gate.night_marked"))
        self.refresh_lists()
        return True

    def lost_ticket_flow(self) -> None:
        dialog = LostTicketDialog(self, self.gate)
        code = dialog.exec()
        session = dialog.selected()
        if session is None:
            return
        if code == LostTicketDialog.DUPLICATE:
            self.print_duplicate(session)
        elif code == LostTicketDialog.COLLECT:
            self.load_exit(session, lost=True)

    # ================================================================ side panel
    def _side_panel(self) -> QWidget:
        card = Card()
        card.setMinimumWidth(SIDE_PANEL_MIN_WIDTH)
        self.side_tabs = QTabWidget()
        inside_page = QWidget()
        inside_layout = QVBoxLayout(inside_page)
        inside_layout.setContentsMargins(0, Space.S, 0, 0)
        self.inside_search = TextField(tr("gate.inside_search"))
        self.inside_search.textChanged.connect(lambda _t: self.inside_model.reset())
        inside_layout.addWidget(self.inside_search)
        self.inside_model = LazyTableModel(self._inside_columns(), self._fetch_inside)
        self.inside_table = DataTable(self.inside_model)
        self.inside_table.setItemDelegateForColumn(0, PlateDelegate(plate_of, self.inside_table))
        self.inside_table.doubleClicked.connect(lambda _i: self._exit_selected())
        inside_layout.addWidget(self.inside_table, 1)
        inside_layout.addWidget(
            Button(tr("gate.night_list"), "clipboard-list", variant="ghost", on_click=self.print_night_list)
        )
        self.side_tabs.addTab(inside_page, tr("gate.inside"))
        self.today_model = LazyTableModel(
            [
                Column(tr("gate.col_plate"), lambda _e: "", width=150),
                Column(tr("gate.col_time"), lambda e: fa_time(e.entry_at_utc), width=70),
                Column(tr("gate.col_ticket"), lambda e: fa_ltr(e.ticket_no)),
            ],
            self._fetch_today,
        )
        self.today_table = DataTable(self.today_model)
        self.today_table.setItemDelegateForColumn(0, PlateDelegate(plate_of, self.today_table))
        self.side_tabs.addTab(self.today_table, tr("gate.today"))
        self.side_tabs.addTab(
            EmptyState("camera-off", tr("gate.unidentified_empty"), tr("gate.unidentified_hint")),
            tr("gate.unidentified"),
        )
        card.add(self.side_tabs, 1)
        return card

    def _inside_columns(self) -> list[Column]:
        return [
            Column(tr("gate.col_plate"), lambda _s: "", width=150),
            Column(tr("gate.col_time"), lambda s: fa_time(s.entry_at_utc), width=70),
            Column(tr("gate.col_type"), lambda s: tr(f"vehicle.{s.vehicle_type}"), width=80),
            Column(tr("gate.col_marks"), self._marks),
        ]

    @staticmethod
    def _marks(session: ActiveSession) -> str:
        marks = []
        if session.night_marked:
            marks.append(tr("gate.mark_night"))
        if session.duplicate_count:
            marks.append(tr("gate.mark_duplicate"))
        if session.kind == VisitKind.PASS_THROUGH.value:
            marks.append(tr("visit.pass_through"))
        return "، ".join(marks)

    def _fetch_inside(self, offset: int, limit: int) -> list[ActiveSession]:
        return self.gate.search_inside(self.inside_search.value(), offset, limit)

    def _fetch_today(self, offset: int, limit: int) -> list[EntryEvent]:
        return self.gate.todays_entries(offset, limit)

    def _exit_selected(self) -> None:
        session = self.inside_table.selected_object()
        if session is not None:
            self.load_exit(session)

    # ================================================================ status strip
    def _status_strip(self) -> QWidget:
        strip = Card()
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.levels_box = QHBoxLayout()
        self.levels_box.setSpacing(Space.L)
        row.addLayout(self.levels_box, 3)
        self.counter_chips: dict[str, QLabel] = {}
        counters = QHBoxLayout()
        counters.setSpacing(Space.XS)
        for key in ("total", "transient", "motorcycle", "pass_through", "subscriber", "free"):
            item = chip("", "accent" if key == "total" else "neutral")
            self.counter_chips[key] = item
            counters.addWidget(item)
        row.addLayout(counters, 4)
        strip.body().addLayout(row)
        return strip

    def refresh_status(self) -> None:
        while self.levels_box.count():
            item = self.levels_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for level in self.gate.occupancy():
            holder = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(Space.XXS)
            column.addWidget(
                label(
                    tr(
                        "gate.level_status",
                        name=level.name,
                        inside=fa_number(level.inside),
                        capacity=fa_number(level.capacity),
                    ),
                    "caption",
                )
            )
            bar = QProgressBar()
            bar.setRange(0, max(1, level.capacity))
            bar.setValue(min(level.inside, level.capacity))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            column.addWidget(bar)
            self.levels_box.addWidget(holder)
        counts = self.gate.counters()
        for key, counter in self.counter_chips.items():
            counter.setText(tr(f"counter.{key}", n=fa_number(counts.get(key, 0))))

    def refresh_lists(self) -> None:
        self.inside_model.reset()
        self.today_model.reset()
        self.refresh_status()
        self.subtitle_label.setText(tr("gate.subtitle", n=fa_number(self.gate.inside_count())))

    # ================================================================ alerts, keys, lifecycle
    def _main_window(self) -> object | None:
        window = self.window()
        return window if hasattr(window, "alerts") else None

    def _alert(self, key: str, text: str, level: str) -> None:
        window = self._main_window()
        if window is not None:
            window.alerts.raise_alert(Alert(key, text, level))  # type: ignore[attr-defined]

    def _clear_alert(self, key: str) -> None:
        window = self._main_window()
        if window is not None:
            window.alerts.clear_alert(key)  # type: ignore[attr-defined]

    def _shortcuts(self) -> None:
        actions: dict[str, Callable[[], object]] = {
            "F2": self.print_without_plate,
            "F3": self.toggle_motorcycle,
            "F4": self.open_pass_menu,
            "F5": self.focus_ticket,
            "F6": self.calculate,
            "F7": lambda: self.pay(PaymentMethod.CASH),
            "F8": lambda: self.pay(PaymentMethod.CARD),
            "F9": lambda: self.pay(PaymentMethod.MALL_CARD),
            "F10": self.flee_current,
            "F11": self.mark_night_current,
            "F12": self.lost_ticket_flow,
        }
        for keys, action in actions.items():
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        for keys, text in SHORTCUTS:
            add_shortcut_row(keys, text)

    def focus_ticket(self) -> None:
        self.ticket_field.setFocus()
        self.ticket_field.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Space / Enter print the entry receipt when the focus is not in a text field."""
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            if not isinstance(focus, QLineEdit):
                self.print_entry()
                return
        super().keyPressEvent(event)

    def on_show(self) -> None:
        if isinstance(self.scanner, WedgeScanner):
            self.scanner.install()
        elif isinstance(self.scanner, SerialScanner):
            self.scanner.start()
        flagged = self.gate.auto_flag_overnight()
        if flagged:
            show_toast(self, tr("gate.overnight_flagged", n=fa_number(len(flagged))), "warning")
        self.refresh_lists()
        self.plate_input.focus_first()

    def hideEvent(self, event: object) -> None:
        if isinstance(self.scanner, WedgeScanner):
            self.scanner.uninstall()
        super().hideEvent(event)  # type: ignore[arg-type]

    def closeEvent(self, event: object) -> None:
        if isinstance(self.scanner, SerialScanner):
            self.scanner.stop()
        super().closeEvent(event)  # type: ignore[arg-type]

    def print_night_list(self) -> bool:
        """At closing: printable list of vehicles still inside, for security (SPEC §4.10)."""
        sessions = self.gate.night_list()
        rows = [InsideRow(plate_of(s), s.entry_at_utc, s.ticket_no, tr(f"vehicle.{s.vehicle_type}")) for s in sessions]
        image = render_inside_list(tr("gate.night_list_title"), self.ctx.clock.now_utc(), rows)
        try:
            self.printing.printer.print_image(image, "night-list")
        except PrinterError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("gate.night_list_printed", n=fa_number(len(rows))))
        return True
