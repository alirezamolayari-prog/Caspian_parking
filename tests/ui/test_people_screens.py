"""Subscribers, shops, free access, blocklist screens and the gate banners/alarm (Phase 4)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from caspian_parking.app import build_main_window
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services.blocklist import BlocklistService
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.subscriber_import import FIELDS
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.screens.gate import GateScreen
from caspian_parking.ui.screens.subscribers import PayDialog
from caspian_parking.ui.widgets.alerts import AlarmOverlay

MON = date(2026, 9, 28)


def at(day: date, hh: int = 10) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh)))


@pytest.fixture
def window(qtbot, admin_ctx, clock):
    clock.set(at(MON))
    main = build_main_window(admin_ctx)
    qtbot.addWidget(main)
    main.resize(1500, 900)
    return main


def test_admin_sidebar_has_phase4_screens(window):
    keys = [s.key for s in window.screens]
    assert keys[:6] == ["gate", "home", "subscribers", "shops", "free_access", "blocklist"]


def test_subscriber_create_pay_and_follow_up(window, admin_ctx, clock, monkeypatch):
    screen = window.show_screen("subscribers")
    profile = screen.profile
    screen.new()
    profile.first.setText("علی")
    profile.last.setText("رضایی")
    profile.mobile.setText("09121234567")
    profile.plate_input.set_text("12ب345-22")
    person = profile.save()
    assert person is not None
    assert screen.model.loaded_count() == 1
    assert profile.person.id == person.id
    assert len(PeopleService(admin_ctx).plates_of(person.id)) == 1
    # a second plate, then payment through the dialog (auto-accepted)
    profile.plate_input.set_text("55ج777-11")
    assert profile.add_plate()
    assert profile.plates_box.count() == 2
    assert profile.pay(auto_accept=True)
    assert "۳۰" in profile.days.text()
    # expire → appears in the follow-up list, exports work
    clock.advance(days=31)
    screen.reload(select_id=person.id)
    assert screen.follow_model.loaded_count() == 1
    excel = screen.export("excel", open_file=False)
    word = screen.export("word", open_file=False)
    assert excel.is_file()
    assert word.is_file()


def test_pay_dialog_shows_negative_days(window, admin_ctx, clock, qtbot):
    service = PeopleService(admin_ctx)
    person = service.create_person(PersonInput(first_name="ناصر", plates=[(parse_plate("12ب345-22"), None)]))
    service.pay_subscription(person.id, "cash")
    service.allow_negative(person.id, 10, "ok")
    gate = GateScreen(admin_ctx, ReceiptPrinting(admin_ctx, SimulatorPrinter()))
    qtbot.addWidget(gate)
    clock.set(at(MON) + timedelta(days=32))
    gate.plate_input.set_text("12ب345-22")
    assert gate.identity_banner.isVisibleTo(gate)
    assert "منفی" in gate.identity_detail.text()
    gate.print_entry()
    assert gate.printing.printer.printed == []  # covered visit: no paper ticket
    clock.advance(days=1)
    dialog = PayDialog(window, service, service.get(person.id))
    qtbot.addWidget(dialog)
    texts = [
        dialog.content.itemAt(i).widget().text()
        for i in range(dialog.content.count())
        if dialog.content.itemAt(i).widget()
    ]
    assert any("۱ روز استفاده" in t for t in texts)
    assert dialog.pay()


def test_profile_negative_and_exempt_buttons(window, admin_ctx):
    screen = window.show_screen("subscribers")
    service = PeopleService(admin_ctx)
    person = service.create_person(PersonInput(first_name="مینا"))
    screen.reload(select_id=person.id)
    assert screen.profile.allow_negative(reason="قول داد")
    assert service.get(person.id).negative_allowed
    assert screen.profile.toggle_exempt(reason="نگهبان")
    assert service.get(person.id).night_exempt


def test_import_tab(window, admin_ctx, tmp_path):
    screen = window.show_screen("subscribers")
    template = screen.save_template()
    assert template.is_file()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(FIELDS))
    sheet.append(["رضا", "", "", "", "", "", "13ب345-22", "", "", "", ""])
    sheet.append(["", "", "", "", "", "", "bad", "", "", "", ""])
    path = tmp_path / "in.xlsx"
    workbook.save(path)
    screen.run_import(Path(path))
    assert "۱" in screen.import_result.text()
    assert screen.model.loaded_count() == 1


def test_shops_screen_deposit_statement_and_renew(window, admin_ctx):
    screen = window.show_screen("shops")
    screen.new()
    screen.name.setText("مبلمان آرتا")
    shop = screen.save()
    assert shop is not None
    service = PeopleService(admin_ctx)
    service.create_person(
        PersonInput(first_name="فروشنده", shop_id=shop.id, payer="shop", plates=[(parse_plate("12ب345-22"), None)])
    )
    screen.deposit_amount.set_value(9_000_000)
    assert screen.deposit()
    assert screen.renew_due() == 1
    assert screen.statement_model.loaded_count() == 2
    assert "۵٬۰۰۰٬۰۰۰" in screen.balance.text()
    assert screen.export("excel", open_file=False).is_file()


def test_free_access_screen(window, admin_ctx):
    screen = window.show_screen("free_access")
    screen.first.setText("مهمان")
    screen.category.setCurrentIndex(screen.category.findData("guest"))
    screen.plate_input.set_text("12ب345-22")
    person = screen.save()
    assert person is not None
    assert screen.add_permit()
    assert "←" in screen.permits.text()


def test_blocklist_screen_and_gate_alarm(window, admin_ctx, qtbot):
    screen = window.show_screen("blocklist")
    screen.plate_input.set_text("12ب345-22")
    screen.description.setText("بدهی")
    assert screen.add() is not None
    assert screen.model.loaded_count() == 1
    gate = window.show_screen("gate")
    gate.printing.printer = SimulatorPrinter()
    gate.plate_input.set_text("12ب345-22")
    assert gate.identity_banner.property("banner") == "danger"
    assert gate.print_entry() is None
    alarms = window.findChildren(AlarmOverlay)
    assert len(alarms) == 1
    alarms[0].dismiss()
    screen = window.show_screen("blocklist")
    screen.attempts_model.reset()
    assert screen.attempts_model.loaded_count() == 1
    screen.table.selectRow(0)
    assert screen.unblock(reason="تسویه")
    assert BlocklistService(admin_ctx).active() == []


def test_security_block_shows_generic_message_to_operators(qtbot, operator_ctx):
    from caspian_parking.services import auth
    from caspian_parking.services.auth import CurrentUser

    operator = operator_ctx.user
    with operator_ctx.uow() as session:
        boss = auth.create_user(session, "boss2", "Boss", "secret1", preset="admin")
    operator_ctx.user = CurrentUser.from_user(boss)  # only a supervisor/admin may create blocks
    BlocklistService(operator_ctx).block_plate(parse_plate("12ب345-22"), "security", "جزئیات محرمانه")
    operator_ctx.user = operator
    gate = GateScreen(operator_ctx, ReceiptPrinting(operator_ctx, SimulatorPrinter()))
    qtbot.addWidget(gate)
    gate.plate_input.set_text("12ب345-22")
    assert "محرمانه" not in gate.identity_detail.text()
    assert "سرپرست" in gate.identity_detail.text()
