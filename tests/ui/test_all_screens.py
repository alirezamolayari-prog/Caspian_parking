"""Phase 11 QA: every screen opens in both themes with realistic data, renders, and uses no missing Persian
string. Screenshots go to tests/artifacts/screens/ for visual review."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from caspian_parking.app import build_main_window
from caspian_parking.config.machine import CameraConfig
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.services.ads import AdService
from caspian_parking.services.coupons import CouponService
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.ui.shell.registry import all_screens
from caspian_parking.ui.theme.manager import ThemeManager

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "screens"
MON = date(2026, 9, 28)


def populate(ctx, clock) -> None:
    people = PeopleService(ctx)
    shop = people.create_shop("مبلمان آرتا", unit="همکف ۱۲")
    people.deposit(shop.id, 5_000_000, "cash")
    person = people.create_person(
        PersonInput(first_name="علی", last_name="رضایی", shop_id=shop.id, plates=[(parse_plate("12ب345-22"), None)])
    )
    people.pay_subscription(person.id, "card")
    ads = AdService(ctx)
    ads.create_ad(shop.id, "gold", MON, MON + timedelta(days=20), offer="۲۰٪ تخفیف ویژه")
    CouponService(ctx).sell_batch(shop.id, 3, "wallet")
    gate = GateService(ctx)
    for plate in ("55ج777-11", "31د456-44", "123-45678"):
        gate.register_entry(parse_plate(plate))
        clock.advance(minutes=7)
    entry = gate.register_entry(parse_plate("44د111-55"))
    clock.advance(minutes=45)
    gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)


@pytest.mark.slow
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_screen_in_both_themes(qtbot, admin_ctx, clock, caplog, theme):
    clock.set(local_to_utc(datetime.combine(MON, time(10))))
    admin_ctx.config.cameras = [CameraConfig(lane="entry", name="ورودی", kind="simulator")]
    populate(admin_ctx, clock)
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    ThemeManager.instance().apply(theme)
    window.resize(1440, 900)
    window.show()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    keys = [spec.key for spec in all_screens()]
    assert len(keys) >= 15
    caplog.set_level(logging.WARNING, logger="caspian_parking.i18n")
    for key in keys:
        page = window.show_screen(key)
        assert page is not None, key
        if hasattr(page, "on_show"):
            page.on_show()
        QApplication.processEvents()
        image = window.grab().toImage()
        assert image.width() >= 1400, key
        assert not image.isNull(), key
        image.save(str(ARTIFACTS / f"{theme}_{key}.png"))
    missing = [r.getMessage() for r in caplog.records if "missing translation key" in r.getMessage()]
    assert missing == []
    page = window.show_screen("gate")
    page.stop_cameras()
