"""Advertising (SPEC §4.11): ad contracts, coupons, ad calendar, monthly raffle, receipt templates,
ad display screen."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.coupons import format_coupon_code
from caspian_parking.core.jalali import jalali_of
from caspian_parking.core.receipt import CouponReceipt
from caspian_parking.data.models import Ad, Coupon, CouponBatch, Shop
from caspian_parking.devices.printer import PrinterError
from caspian_parking.i18n import tr
from caspian_parking.i18n.bidi import ltr
from caspian_parking.i18n.format import fa_date, fa_digits, fa_money
from caspian_parking.services import templates
from caspian_parking.services.ads import PACKAGES, AdError, AdService, ad_light
from caspian_parking.services.context import AppContext
from caspian_parking.services.coupons import CouponError, CouponService, CouponStatus
from caspian_parking.services.people import PeopleService
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.receipt.renderer import WIDTH, load_mono_image
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.gate_dialogs import ask_reason
from caspian_parking.ui.screens.settings_devices import sample_content
from caspian_parking.ui.theme.tokens import Size, Space
from caspian_parking.ui.widgets.alerts import LightDelegate
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, label, set_chip
from caspian_parking.ui.widgets.feedback import show_toast
from caspian_parking.ui.widgets.inputs import JalaliDateEdit, MoneyField
from caspian_parking.ui.widgets.slideshow import SlideshowWindow
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

PAYMENT_METHODS = ("cash", "card", "mall_card")
COUPON_METHODS = (*PAYMENT_METHODS, "wallet")
WEEK_ORDER = (5, 6, 0, 1, 2, 3, 4)  # Saturday first (Python weekday numbers)
TEMPLATE_MAX_HEIGHT = 700
MAX_COUPONS_PER_TICK = 5


def _page() -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
    layout.setSpacing(Space.L)
    return page, layout


def _shop_combo(shops: list[Shop], empty_key: str | None = None) -> QComboBox:
    combo = QComboBox()
    if empty_key is not None:
        combo.addItem(tr(empty_key), None)
    for shop in shops:
        combo.addItem(shop.name, shop.id)
    return combo


def _grid(pairs: list[tuple[str, QWidget]], columns: int = 2) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(Space.M)
    grid.setVerticalSpacing(Space.XS)
    for index, (key, widget) in enumerate(pairs):
        row, column = (index // columns) * 2, index % columns
        grid.addWidget(label(tr(key), "caption"), row, column)
        grid.addWidget(widget, row + 1, column)
    return grid


def mono_pixmap(path: Path | None, width: int = Size.TEMPLATE_PREVIEW) -> QPixmap | None:
    """1-bit preview that matches the print."""
    image = load_mono_image(path, WIDTH, TEMPLATE_MAX_HEIGHT)
    if image is None:
        return None
    return QPixmap.fromImage(image).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)


class AdsScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("ads.title"), tr("ads.subtitle"))
        self.ads = AdService(ctx)
        self.coupons = CouponService(ctx)
        self.people = PeopleService(ctx)
        self.printing = ReceiptPrinting(ctx)
        self.ad: Ad | None = None
        self.slideshow: SlideshowWindow | None = None
        self._coupon_queue: list[CouponReceipt] = []
        self._shops = self.people.shops("")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._ads_tab(), tr("ads.tab_ads"))
        self.tabs.addTab(self._coupons_tab(), tr("ads.tab_coupons"))
        self.tabs.addTab(self._calendar_tab(), tr("ads.tab_calendar"))
        self.tabs.addTab(self._raffle_tab(), tr("ads.tab_raffle"))
        self.tabs.addTab(self._templates_tab(), tr("ads.tab_templates"))
        self.tabs.addTab(self._screen_tab(), tr("ads.tab_screen"))
        self.body.addWidget(self.tabs, 1)
        self.new_ad()

    # ================================================================ ads
    def _ads_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        row.setSpacing(Space.L)
        list_card = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("ads.list"), "title"), 1)
        head.addWidget(Button(tr("ads.new"), "megaphone", variant="primary", on_click=self.new_ad))
        list_card.body().addLayout(head)
        now = self.ctx.clock.now_utc
        counts: dict[str, int] = {}
        columns = [
            Column("", lambda _a: "", width=40),
            Column(tr("ads.col_shop"), lambda a: self._shop_name(a.shop_id), width=150),
            Column(tr("ads.col_package"), lambda a: tr(f"ads.package.{a.package}"), width=80),
            Column(tr("ads.col_dates"), lambda a: f"{fa_date(a.start_date)} ← {fa_date(a.end_date)}", width=190),
            Column(tr("ads.col_prints"), lambda a: fa_digits(counts.get(a.id, 0))),
        ]

        def fetch(offset: int, limit: int) -> list[Ad]:
            rows = self.ads.all_ads(offset, limit)
            counts.update(self.ads.print_counts([a.id for a in rows]))
            return rows

        self.ads_model = LazyTableModel(columns, fetch)
        self.ads_table = DataTable(self.ads_model)
        self.ads_table.setItemDelegateForColumn(
            0,
            LightDelegate(
                lambda a: (ad_light(a, now()).value if a.is_active else "black") if a else None, self.ads_table
            ),
        )
        self.ads_table.clicked.connect(lambda _i: self.show_ad(self.ads_table.selected_object()))
        list_card.add(self.ads_table, 1)
        row.addWidget(list_card, 3)

        form = Card()
        form_head = QHBoxLayout()
        self.ad_title = label(tr("ads.new"), "h3")
        form_head.addWidget(self.ad_title, 1)
        self.ad_status = chip("", "neutral")
        form_head.addWidget(self.ad_status)
        form.body().addLayout(form_head)
        self.ad_shop = _shop_combo(self._shops)
        self.ad_package = QComboBox()
        for package in PACKAGES:
            self.ad_package.addItem(tr(f"ads.package.{package}"), package)
        self.ad_package.currentIndexChanged.connect(self._package_changed)
        today = jalali_of(self.ctx.clock.now_utc())
        self.ad_start = JalaliDateEdit(today)
        self.ad_end = JalaliDateEdit(today)
        self.ad_end.set_gregorian(self.ad_start.gregorian() + timedelta(days=29))
        self.ad_price = MoneyField()
        self.ad_method = QComboBox()
        self.ad_method.addItem(tr("ads.not_paid"), None)
        for method in PAYMENT_METHODS:
            self.ad_method.addItem(tr(f"payment.{method}"), method)
        form.body().addLayout(
            _grid(
                [
                    ("ads.col_shop", self.ad_shop),
                    ("ads.col_package", self.ad_package),
                    ("ads.start", self.ad_start),
                    ("ads.end", self.ad_end),
                    ("ads.col_price", self.ad_price),
                    ("ads.payment", self.ad_method),
                ]
            )
        )
        self.ad_text = QPlainTextEdit()
        self.ad_text.setPlaceholderText(tr("ads.text_hint"))
        self.ad_text.setMaximumHeight(Size.ROW * 2)
        self.ad_offer = TextField(tr("ads.offer"))
        self.ad_location = TextField(tr("ads.location"))
        form.add(self.ad_text)
        line = QHBoxLayout()
        line.addWidget(self.ad_offer, 1)
        line.addWidget(self.ad_location, 1)
        form.body().addLayout(line)
        days = QHBoxLayout()
        days.addWidget(label(tr("ads.weekdays"), "caption"))
        self.ad_days: dict[int, QCheckBox] = {}
        for weekday in WEEK_ORDER:
            box = QCheckBox(tr(f"weekday.{weekday}"))
            self.ad_days[weekday] = box
            days.addWidget(box)
        days.addStretch(1)
        form.body().addLayout(days)
        flags = QHBoxLayout()
        self.ad_large = QCheckBox(tr("ads.large"))
        self.ad_on_entry = QCheckBox(tr("ads.on_entry"))
        self.ad_on_exit = QCheckBox(tr("ads.on_exit"))
        for box in (self.ad_large, self.ad_on_entry, self.ad_on_exit):
            flags.addWidget(box)
        flags.addStretch(1)
        form.body().addLayout(flags)
        logo = QHBoxLayout()
        self.ad_logo_label = label("", "muted")
        self.ad_logo: Path | None = None
        self.ad_logo_button = Button(tr("ads.logo"), "image", on_click=self.pick_logo)
        logo.addWidget(self.ad_logo_button)
        logo.addWidget(self.ad_logo_label, 1)
        form.body().addLayout(logo)
        actions = QHBoxLayout()
        self.end_button = Button(tr("ads.end_contract"), "ban", variant="ghost", on_click=self.end_current)
        actions.addWidget(self.end_button)
        actions.addStretch(1)
        actions.addWidget(Button(tr("common.save"), "check", variant="primary", on_click=self.save_ad))
        form.body().addLayout(actions)
        row.addWidget(form, 4)
        return page

    def _shop_name(self, shop_id: str) -> str:
        return next((s.name for s in self._shops if s.id == shop_id), "—")

    def _package_price(self, package: str) -> int:
        with self.ctx.read() as session:
            packages = get_setting(session, "ads.packages") or {}
        return int((packages.get(package) or {}).get("price", 0))

    def _package_changed(self) -> None:
        package = self.ad_package.currentData()
        if self.ad is None:
            self.ad_price.set_value(self._package_price(package))
        with self.ctx.read() as session:
            features = (get_setting(session, "ads.packages") or {}).get(package, {}).get("features", [])
        self.ad_logo_button.setEnabled("logo" in features)

    def new_ad(self) -> None:
        self.show_ad(None)

    def show_ad(self, ad: Ad | None) -> None:
        self.ad = ad
        self.ad_title.setText(self._shop_name(ad.shop_id) if ad else tr("ads.new"))
        self.ad_shop.setEnabled(ad is None)
        if ad is not None:
            self.ad_shop.setCurrentIndex(max(0, self.ad_shop.findData(ad.shop_id)))
            self.ad_package.setCurrentIndex(max(0, self.ad_package.findData(ad.package)))
            self.ad_start.set_gregorian(ad.start_date)
            self.ad_end.set_gregorian(ad.end_date)
            self.ad_price.set_value(ad.price)
        else:
            self.ad_package.setCurrentIndex(0)
            self._package_changed()
        self.ad_method.setCurrentIndex(0)
        self.ad_method.setEnabled(ad is None)
        self.ad_text.setPlainText(ad.text if ad else "")
        self.ad_offer.setText(ad.offer if ad else "")
        self.ad_location.setText(ad.location if ad else "")
        for weekday, box in self.ad_days.items():
            box.setChecked(bool(ad and weekday in (ad.weekdays or [])))
        self.ad_large.setChecked(bool(ad and ad.large))
        self.ad_on_entry.setChecked(ad.on_entry if ad else True)
        self.ad_on_exit.setChecked(bool(ad and ad.on_exit))
        self.ad_logo = None
        self.ad_logo_label.setText(ltr(ad.logo_file) if ad and ad.logo_file else tr("ads.no_logo"))
        self.end_button.setVisible(bool(ad and ad.is_active))
        self.ad_status.setVisible(ad is not None)
        if ad is not None:
            light = ad_light(ad, self.ctx.clock.now_utc()) if ad.is_active else None
            kind = {"green": "success", "amber": "warning"}.get(light.value if light else "", "danger")
            set_chip(self.ad_status, tr(f"ads.status.{light.value}" if light else "ads.status.ended"), kind)

    def pick_logo(self, path: Path | None = None) -> Path | None:
        if path is None:  # pragma: no cover - native dialog
            chosen, _filter = QFileDialog.getOpenFileName(self, tr("ads.logo"), "", "Images (*.png *.jpg *.jpeg)")
            path = Path(chosen) if chosen else None
        if path is not None:
            self.ad_logo = path
            self.ad_logo_label.setText(ltr(path.name))
        return path

    def _weekdays(self) -> list[int]:
        chosen = [day for day, box in self.ad_days.items() if box.isChecked()]
        return [] if len(chosen) == len(self.ad_days) else chosen

    def save_ad(self) -> Ad | None:
        fields = {
            "text": self.ad_text.toPlainText(),
            "offer": self.ad_offer.value(),
            "location": self.ad_location.value(),
            "large": self.ad_large.isChecked(),
            "weekdays": self._weekdays(),
            "on_entry": self.ad_on_entry.isChecked(),
            "on_exit": self.ad_on_exit.isChecked(),
        }
        try:
            if self.ad is None:
                shop_id = self.ad_shop.currentData()
                if shop_id is None:
                    show_toast(self, tr("ads.need_shop"), "warning")
                    return None
                ad = self.ads.create_ad(
                    shop_id,
                    self.ad_package.currentData(),
                    self.ad_start.gregorian(),
                    self.ad_end.gregorian(),
                    price=self.ad_price.value(),
                    logo_source=self.ad_logo,
                    method=self.ad_method.currentData(),
                    **fields,  # type: ignore[arg-type]
                )
            else:
                if self.ad_end.gregorian() < self.ad_start.gregorian():
                    raise AdError("people.bad_range")
                ad = self.ads.update_ad(
                    self.ad.id,
                    package=self.ad_package.currentData(),
                    start_date=self.ad_start.gregorian(),
                    end_date=self.ad_end.gregorian(),
                    price=self.ad_price.value(),
                    **fields,
                )
        except AdError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("common.saved"))
        self.ads_model.reset()
        self.show_ad(ad)
        return ad

    def end_current(self, reason: str | None = None) -> bool:
        if self.ad is None:
            return False
        if reason is None:  # pragma: no cover - dialog
            reason = ask_reason(self, tr("ads.end_contract"))
            if not reason:
                return False
        try:
            self.ads.end_ad(self.ad.id, reason)
        except AdError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        self.ads_model.reset()
        self.new_ad()
        return True

    # ================================================================ coupons
    def _coupons_tab(self) -> QWidget:
        page, layout = _page()
        sell = Card(tr("coupons.sell"))
        self.coupon_shop = _shop_combo(self._shops)
        self.coupon_quantity = QSpinBox()
        self.coupon_quantity.setRange(1, 1000)
        self.coupon_quantity.setValue(10)
        self.coupon_method = QComboBox()
        for method in COUPON_METHODS:
            self.coupon_method.addItem(tr(f"payment.{method}"), method)
        self.coupon_template = QComboBox()
        self.coupon_price = label("", "title")
        self.coupon_quantity.valueChanged.connect(self._coupon_price_changed)
        sell.body().addLayout(
            _grid(
                [
                    ("ads.col_shop", self.coupon_shop),
                    ("coupons.quantity", self.coupon_quantity),
                    ("ads.payment", self.coupon_method),
                    ("coupons.template", self.coupon_template),
                ],
                columns=4,
            )
        )
        line = QHBoxLayout()
        line.addWidget(self.coupon_price, 1)
        line.addWidget(Button(tr("coupons.sell_print"), "ticket-percent", variant="primary", on_click=self.sell))
        sell.body().addLayout(line)
        layout.addWidget(sell)

        lists = QHBoxLayout()
        batches = Card(tr("coupons.batches"))
        batch_columns = [
            Column(tr("coupons.col_date"), lambda b: fa_date(b.created_at_utc), width=100),
            Column(tr("ads.col_shop"), lambda b: self._shop_name(b.shop_id), width=150),
            Column(tr("coupons.quantity"), lambda b: fa_digits(b.quantity), width=70),
            Column(tr("coupons.col_total"), lambda b: fa_money(b.total, False), width=110),
            Column(tr("coupons.col_expiry"), lambda b: fa_date(b.expires_on)),
        ]
        self.batch_model = LazyTableModel(batch_columns, lambda o, lim: self.coupons.batches()[o : o + lim])
        self.batch_table = DataTable(self.batch_model)
        self.batch_table.clicked.connect(lambda _i: self.show_batch(self.batch_table.selected_object()))
        batches.add(self.batch_table, 1)
        reprint = QHBoxLayout()
        reprint.addStretch(1)
        reprint.addWidget(Button(tr("coupons.reprint"), "printer", on_click=self.reprint_batch))
        batches.body().addLayout(reprint)
        lists.addWidget(batches, 3)
        codes = Card(tr("coupons.codes"))
        self._codes: list[tuple[Coupon, CouponStatus]] = []
        code_columns = [
            Column(tr("coupons.code"), lambda r: ltr(fa_digits(format_coupon_code(r[0].code))), width=170),
            Column(tr("rep.col_status"), lambda r: tr(f"coupons.status.{r[1].value}")),
        ]
        self.code_model = LazyTableModel(code_columns, lambda o, lim: self._codes[o : o + lim])
        codes.add(DataTable(self.code_model), 1)
        lists.addWidget(codes, 2)
        stats = Card(tr("coupons.stats"))
        stat_columns = [
            Column(tr("ads.col_shop"), lambda s: s.shop_name, width=130),
            Column(tr("coupons.bought"), lambda s: fa_digits(s.bought), width=60),
            Column(tr("coupons.used_count"), lambda s: fa_digits(s.used), width=60),
            Column(tr("coupons.expired_count"), lambda s: fa_digits(s.expired), width=60),
            Column(tr("coupons.revenue"), lambda s: fa_money(s.revenue, False)),
        ]
        self.stats_model = LazyTableModel(stat_columns, lambda o, lim: self.coupons.stats()[o : o + lim])
        stats.add(DataTable(self.stats_model), 1)
        lists.addWidget(stats, 3)
        layout.addLayout(lists, 1)
        self._coupon_timer = QTimer(self)
        self._coupon_timer.setInterval(0)
        self._coupon_timer.timeout.connect(self._print_next_coupons)
        return page

    def _unit_price(self) -> int:
        with self.ctx.read() as session:
            return int(get_setting(session, "coupons.unit_price"))

    def _coupon_price_changed(self) -> None:
        total = self._unit_price() * self.coupon_quantity.value()
        self.coupon_price.setText(tr("coupons.total", amount=fa_money(total)))

    def sell(self) -> CouponBatch | None:
        shop_id = self.coupon_shop.currentData()
        if shop_id is None:
            show_toast(self, tr("ads.need_shop"), "warning")
            return None
        try:
            batch, coupons = self.coupons.sell_batch(
                shop_id,
                self.coupon_quantity.value(),
                self.coupon_method.currentData(),
                template_file=self.coupon_template.currentData(),
            )
        except CouponError as exc:
            show_toast(self, tr(str(exc)), "error")
            return None
        show_toast(self, tr("coupons.sold", n=fa_digits(len(coupons))))
        self.print_coupons(batch, coupons)
        self.batch_model.reset()
        self.stats_model.reset()
        self.show_batch(batch)
        return batch

    def print_coupons(self, batch: CouponBatch, coupons: list[Coupon]) -> None:
        """Queue the coupons; they are printed a few per event-loop tick so the UI stays responsive."""
        template = self.ctx.data_root.templates / batch.template_file if batch.template_file else None
        shop = self._shop_name(batch.shop_id)
        self._coupon_queue.extend(
            CouponReceipt(c.code, shop, c.expires_on, template, training=self.ctx.training) for c in coupons
        )
        self._coupon_timer.start()

    def _print_next_coupons(self) -> None:
        for _ in range(MAX_COUPONS_PER_TICK):
            if not self._coupon_queue:
                self._coupon_timer.stop()
                return
            coupon = self._coupon_queue.pop(0)
            result = self.printing.render_coupon(coupon)
            try:
                self.printing.print_result(result, "coupon")
            except PrinterError as exc:
                self._coupon_queue.clear()
                self._coupon_timer.stop()
                show_toast(self, tr(str(exc)), "error")
                return

    def pending_coupons(self) -> int:
        return len(self._coupon_queue)

    def flush_coupons(self) -> None:
        while self._coupon_queue:
            self._print_next_coupons()

    def show_batch(self, batch: CouponBatch | None) -> None:
        self._codes = self.coupons.coupons_of(batch.id) if batch else []
        self.code_model.reset()

    def reprint_batch(self) -> int:
        batch = self.batch_table.selected_object()
        if batch is None:
            show_toast(self, tr("coupons.select_batch"), "info")
            return 0
        valid = [c for c, status in self.coupons.coupons_of(batch.id) if status is CouponStatus.VALID]
        self.print_coupons(batch, valid)
        return len(valid)

    # ================================================================ calendar
    def _calendar_tab(self) -> QWidget:
        page, layout = _page()
        head = QHBoxLayout()
        today = jalali_of(self.ctx.clock.now_utc())
        self.cal_year = QSpinBox()
        self.cal_year.setRange(1390, 1500)
        self.cal_year.setValue(today.year)
        self.cal_month = QComboBox()
        for month in range(1, 13):
            self.cal_month.addItem(tr(f"jmonth.{month}"), month)
        self.cal_month.setCurrentIndex(today.month - 1)
        self.cal_year.valueChanged.connect(lambda _v: self.load_calendar())
        self.cal_month.currentIndexChanged.connect(lambda _i: self.load_calendar())
        head.addWidget(label(tr("ads.month"), "caption"))
        head.addWidget(self.cal_month)
        head.addWidget(self.cal_year)
        head.addStretch(1)
        self.cal_summary = label("", "muted")
        head.addWidget(self.cal_summary)
        layout.addLayout(head)
        self._calendar: list = []
        columns = [
            Column(tr("ads.cal_day"), lambda d: fa_date(d.day), width=110),
            Column(tr("rep.col_day"), lambda d: tr(f"weekday.{d.day.weekday()}"), width=90),
            Column(tr("ads.cal_sold"), lambda d: fa_digits(d.sold), width=80),
            Column(tr("ads.cal_free"), lambda d: fa_digits(d.free), width=80),
            Column(
                tr("ads.cal_note"),
                lambda d: "، ".join(x for x in (tr("ads.cal_weekend") if d.weekend else "", d.occasion or "") if x),
            ),
        ]
        self.calendar_model = LazyTableModel(columns, lambda o, lim: self._calendar[o : o + lim])
        card = Card()
        card.add(DataTable(self.calendar_model), 1)
        layout.addWidget(card, 1)
        return page

    def load_calendar(self) -> list:
        self._calendar = self.ads.calendar(self.cal_year.value(), self.cal_month.currentData())
        self.calendar_model.reset()
        free = sum(d.free for d in self._calendar)
        sold = sum(d.sold for d in self._calendar)
        self.cal_summary.setText(tr("ads.cal_summary", sold=fa_digits(sold), free=fa_digits(free)))
        return self._calendar

    # ================================================================ raffle
    def _raffle_tab(self) -> QWidget:
        page, layout = _page()
        card = Card(tr("raffle.title"))
        card.add(label(tr("raffle.hint"), "muted", wrap=True))
        today = jalali_of(self.ctx.clock.now_utc())
        self.raffle_year = QSpinBox()
        self.raffle_year.setRange(1390, 1500)
        self.raffle_year.setValue(today.year)
        self.raffle_month = QComboBox()
        for month in range(1, 13):
            self.raffle_month.addItem(tr(f"jmonth.{month}"), month)
        self.raffle_month.setCurrentIndex(today.month - 1)
        self.raffle_sponsor = _shop_combo(self._shops, "raffle.no_sponsor")
        card.body().addLayout(
            _grid(
                [
                    ("ads.month", self.raffle_month),
                    ("raffle.year", self.raffle_year),
                    ("raffle.sponsor", self.raffle_sponsor),
                ],
                columns=3,
            )
        )
        line = QHBoxLayout()
        self.raffle_result = label("", "kpi")
        line.addWidget(self.raffle_result, 1)
        line.addWidget(Button(tr("raffle.draw"), "dices", variant="primary", on_click=self.draw))
        card.body().addLayout(line)
        layout.addWidget(card)
        history = Card(tr("raffle.history"))
        columns = [
            Column(tr("ads.month"), lambda r: ltr(fa_digits(r.month)), width=90),
            Column(tr("raffle.sponsor"), lambda r: self._shop_name(r.sponsor_shop_id or ""), width=150),
            Column(tr("raffle.candidates"), lambda r: fa_digits(r.candidates), width=90),
            Column(tr("raffle.winner"), lambda r: ltr(fa_digits(r.winner_ticket)), width=130),
            Column(tr("raffle.digest"), lambda r: ltr(r.candidates_digest[:16] + "…")),
        ]
        self.raffle_model = LazyTableModel(columns, lambda o, lim: self.ads.raffles()[o : o + lim])
        history.add(DataTable(self.raffle_model), 1)
        layout.addWidget(history, 1)
        return page

    def draw(self) -> str | None:
        try:
            result = self.ads.draw_raffle(
                self.raffle_year.value(), self.raffle_month.currentData(), self.raffle_sponsor.currentData()
            )
        except AdError as exc:
            show_toast(self, tr(str(exc)), "warning")
            return None
        self.raffle_result.setText(tr("raffle.result", ticket=ltr(fa_digits(result.winner_ticket))))
        self.raffle_model.reset()
        return result.winner_ticket

    # ================================================================ templates
    def _templates_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        row.setSpacing(Space.L)
        picker = Card()
        self.selected_label = label("", "title")
        picker.add(self.selected_label)
        filters = QHBoxLayout()
        self.template_search = TextField(tr("templates.search"))
        self.template_search.textChanged.connect(lambda _t: self.refresh_templates())
        self.template_category = QComboBox()
        self.template_category.currentIndexChanged.connect(lambda _i: self.refresh_templates(keep_categories=True))
        filters.addWidget(self.template_search, 2)
        filters.addWidget(self.template_category, 1)
        picker.body().addLayout(filters)
        self._templates: list[templates.Template] = []
        self._visible_templates: list[templates.Template] = []
        columns = [
            Column(tr("templates.name"), lambda t: t.name, width=200),
            Column(tr("templates.category"), lambda t: t.category or tr("templates.no_category")),
        ]
        self.template_model = LazyTableModel(columns, lambda o, lim: self._visible_templates[o : o + lim])
        self.template_table = DataTable(self.template_model)
        self.template_table.clicked.connect(lambda _i: self.preview_template(self.template_table.selected_object()))
        picker.add(self.template_table, 1)
        actions = QHBoxLayout()
        actions.addWidget(Button(tr("templates.use"), "check", variant="primary", on_click=self.use_selected))
        actions.addWidget(Button(tr("templates.mother"), "rotate-ccw", on_click=self.use_mother))
        actions.addWidget(Button(tr("templates.test_print"), "printer", on_click=self.test_print))
        actions.addWidget(Button(tr("templates.edit"), "pencil", on_click=self.edit_selected))
        actions.addWidget(
            Button(tr("templates.open_folder"), "folder-open", variant="ghost", on_click=self.open_folder)
        )
        picker.body().addLayout(actions)
        row.addWidget(picker, 3)
        preview = Card(tr("templates.preview"))
        self.template_preview = QLabel(alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.template_preview.setMinimumWidth(Size.TEMPLATE_PREVIEW)
        preview.add(self.template_preview, 1)
        row.addWidget(preview, 2)
        folder = self.ctx.data_root.templates
        folder.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self.refresh_templates())
        self.refresh_templates()
        return page

    def _watch_folders(self) -> None:
        folder = self.ctx.data_root.templates
        wanted = {str(folder)} | {str(p) for p in folder.rglob("*") if p.is_dir()}
        current = set(self.watcher.directories())
        if wanted - current:
            self.watcher.addPaths(sorted(wanted - current))
        if current - wanted:
            self.watcher.removePaths(sorted(current - wanted))

    def refresh_templates(self, keep_categories: bool = False) -> list[templates.Template]:
        """Re-read the folder (called by the watcher): new files appear, removed ones disappear."""
        self._templates = templates.list_templates(self.ctx.data_root.templates)
        self._watch_folders()
        if not keep_categories:
            chosen = self.template_category.currentData()
            self.template_category.blockSignals(True)
            self.template_category.clear()
            self.template_category.addItem(tr("templates.all_categories"), None)
            for category in templates.categories(self._templates):
                self.template_category.addItem(category or tr("templates.no_category"), category)
            self.template_category.setCurrentIndex(max(0, self.template_category.findData(chosen)))
            self.template_category.blockSignals(False)
        category = self.template_category.currentData()
        visible = templates.search(self._templates, self.template_search.value())
        if category is not None:
            visible = [t for t in visible if t.category == category]
        self._visible_templates = visible
        self.template_model.reset()
        self._refresh_selection_label()
        self._refresh_coupon_templates()
        return visible

    def _refresh_selection_label(self) -> None:
        selection = templates.selected_template(self.ctx)
        if selection.missing:
            text = tr("receipt.template_missing")
        elif selection.path is not None:
            text = tr("templates.current", name=selection.path.stem)
        else:
            text = tr("templates.current_mother")
        self.selected_label.setText(text)

    def _refresh_coupon_templates(self) -> None:
        chosen = self.coupon_template.currentData()
        self.coupon_template.clear()
        self.coupon_template.addItem(tr("coupons.plain"), None)
        for template in self._templates:
            self.coupon_template.addItem(template.name, template.relative)
        self.coupon_template.setCurrentIndex(max(0, self.coupon_template.findData(chosen)))

    def preview_template(self, template: templates.Template | None) -> QPixmap | None:
        pixmap = mono_pixmap(template.path) if template is not None else None
        self.template_preview.setPixmap(pixmap or QPixmap())
        return pixmap

    def use_selected(self) -> bool:
        template = self.template_table.selected_object()
        if template is None:
            show_toast(self, tr("templates.select"), "info")
            return False
        templates.select_template(self.ctx, template.relative)
        self._refresh_selection_label()
        show_toast(self, tr("templates.selected", name=template.name))
        return True

    def use_mother(self) -> None:
        templates.select_template(self.ctx, None)
        self._refresh_selection_label()
        show_toast(self, tr("templates.back_to_mother"))

    def test_print(self) -> bool:
        template = self.template_table.selected_object()
        path = template.path if template is not None else templates.selected_template(self.ctx).path
        result = self.printing.render(sample_content(self.ctx, with_ad=False), path)
        try:
            self.printing.print_result(result, "template-test")
        except PrinterError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        show_toast(self, tr("templates.test_printed"))
        return True

    def edit_selected(self) -> None:  # pragma: no cover - opens Paint
        template = self.template_table.selected_object()
        if template is not None:
            templates.open_in_paint(template.path)

    def open_folder(self) -> None:  # pragma: no cover - opens Explorer
        if hasattr(os, "startfile"):
            os.startfile(self.ctx.data_root.templates)  # type: ignore[attr-defined]

    # ================================================================ ad display screen
    def _screen_tab(self) -> QWidget:
        page, layout = _page()
        card = Card(tr("adscreen.title"))
        card.add(label(tr("adscreen.hint", folder=ltr(str(self.ctx.data_root.slideshow))), "muted", wrap=True))
        with self.ctx.read() as session:
            seconds = int(get_setting(session, "adscreen.seconds"))
        line = QHBoxLayout()
        self.slide_seconds = QSpinBox()
        self.slide_seconds.setRange(1, 600)
        self.slide_seconds.setValue(seconds)
        line.addWidget(label(tr("adscreen.seconds"), "caption"))
        line.addWidget(self.slide_seconds)
        line.addStretch(1)
        line.addWidget(Button(tr("common.save"), "check", on_click=self.save_slideshow))
        line.addWidget(Button(tr("adscreen.open"), "presentation", variant="primary", on_click=self.open_slideshow))
        card.body().addLayout(line)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def save_slideshow(self) -> None:
        with self.ctx.uow() as session:
            set_setting(session, "adscreen.seconds", self.slide_seconds.value())
        if self.slideshow is not None:
            self.slideshow.set_seconds(self.slide_seconds.value())
        show_toast(self, tr("common.saved"))

    def open_slideshow(self) -> SlideshowWindow:
        if self.slideshow is None:
            self.slideshow = SlideshowWindow(self.ctx.data_root.slideshow, self.slide_seconds.value())
        self.slideshow.show_on_best_screen()
        return self.slideshow

    # ================================================================ lifecycle
    def on_show(self) -> None:
        self._shops = self.people.shops("")
        for combo, empty in (
            (self.ad_shop, None),
            (self.coupon_shop, None),
            (self.raffle_sponsor, "raffle.no_sponsor"),
        ):
            _refill(combo, self._shops, empty)
        self.ads_model.reset()
        self.batch_model.reset()
        self.stats_model.reset()
        self.raffle_model.reset()
        self._coupon_price_changed()
        self.load_calendar()
        self.refresh_templates()

    def closeEvent(self, event: object) -> None:  # pragma: no cover - window close
        if self.slideshow is not None:
            self.slideshow.close()
        super().closeEvent(event)  # type: ignore[arg-type]


def _refill(combo: QComboBox, shops: list[Shop], empty_key: str | None) -> None:
    chosen = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if empty_key is not None:
        combo.addItem(tr(empty_key), None)
    for shop in shops:
        combo.addItem(shop.name, shop.id)
    combo.setCurrentIndex(max(0, combo.findData(chosen)))
    combo.blockSignals(False)


__all__ = ["AdsScreen", "mono_pixmap"]
