"""Phase 10: simulators drive the gate — barrier rules and manual opening, RFID subscriber entry/exit and
lost cards, card terminal success / decline / timeout, LED rotation, settings, ad display text slides."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.models import BarrierOpen, Payment
from caspian_parking.devices.barrier import SimulatorBarrier
from caspian_parking.devices.led import SimulatorLed
from caspian_parking.devices.payment import SimulatorTerminal
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services.ads import AdService
from caspian_parking.services.gate_service import PaymentMethod
from caspian_parking.services.hardware import CardService
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.screens.gate import GateScreen

MON = date(2026, 9, 28)
PLATE = parse_plate("12ب345-22")


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def hw_ctx(admin_ctx, clock):
    clock.set(at(MON, 10))
    devices = admin_ctx.config.devices
    devices.barrier_kind = "simulator"
    devices.rfid_kind = "simulator"
    devices.pos_kind = "simulator"
    devices.led_kind = "simulator"
    return admin_ctx


@pytest.fixture
def screen(qtbot, hw_ctx):
    widget = GateScreen(hw_ctx, ReceiptPrinting(hw_ctx, SimulatorPrinter()))
    qtbot.addWidget(widget)
    widget.resize(1500, 900)
    widget.show()
    widget.on_show()
    return widget


def opens(ctx) -> list[tuple[str, str]]:
    with ctx.read() as session:
        return [(b.lane, b.cause) for b in session.scalars(select(BarrierOpen).order_by(BarrierOpen.created_at_utc))]


def subscriber(ctx, card: str):
    people = PeopleService(ctx)
    person = people.create_person(PersonInput(first_name="علی", plates=[(PLATE, None)]))
    people.pay_subscription(person.id, "cash")
    CardService(ctx).assign(person.id, card)
    return person


def test_paid_visit_opens_both_barriers(screen, hw_ctx, clock):
    barrier = screen.barriers.barrier
    assert isinstance(barrier, SimulatorBarrier)
    assert screen.entry_barrier_button.isVisibleTo(screen)
    assert screen.plate_input.set_text(PLATE.key)
    entry = screen.print_entry()
    clock.advance(minutes=30)
    screen.on_scanned(entry.payload)
    assert screen.pay(PaymentMethod.CASH)
    assert barrier.opened == ["entry", "exit"]
    assert opens(hw_ctx) == [("entry", "receipt"), ("exit", "paid")]


def test_manual_open_needs_a_reason(screen, hw_ctx):
    assert not screen.open_barrier_manual("exit", reason=" ")
    assert screen.open_barrier_manual("exit", reason="آمبولانس")
    with hw_ctx.read() as session:
        log = session.scalars(select(BarrierOpen)).one()
    assert (log.lane, log.cause, log.reason) == ("exit", "manual", "آمبولانس")


def test_subscriber_card_entry_and_exit(screen, hw_ctx, clock):
    subscriber(hw_ctx, "E2003412")
    reader = screen.card_reader
    reader.present("E2003412")
    assert screen.gate.inside_count() == 1
    assert screen.printing.printer.printed == []  # covered: no paper ticket
    reader.present("E2003412")  # the reader repeats the tag while the car is still in range: ignored
    assert screen.gate.inside_count() == 1
    clock.advance(hours=2)
    assert screen.on_card("E2003412")  # two hours later at the exit
    assert screen.gate.inside_count() == 0
    assert opens(hw_ctx) == [("entry", "subscriber"), ("exit", "free_exit")]


def test_lost_card_stops_working(screen, hw_ctx):
    person = subscriber(hw_ctx, "AA11")
    card = CardService(hw_ctx).cards_of(person.id)[0]
    CardService(hw_ctx).deactivate(card.id, "مفقود شد")
    assert not screen.on_card("AA11")
    assert screen.gate.inside_count() == 0
    from caspian_parking.services.hardware import HardwareError

    with pytest.raises(HardwareError, match="was_lost"):
        CardService(hw_ctx).assign(person.id, "AA11")


def test_usb_card_reader_through_scanner(screen, hw_ctx):
    subscriber(hw_ctx, "0012345678")
    screen.on_scanned("0012345678")  # USB readers type like a barcode scanner
    assert screen.gate.inside_count() == 1


@pytest.mark.parametrize(("outcome", "paid"), [("approve", True), ("decline", False), ("timeout", False)])
def test_card_terminal(screen, hw_ctx, clock, qtbot, outcome, paid):
    screen.terminal = SimulatorTerminal(outcome)
    assert screen.plate_input.set_text(PLATE.key)
    entry = screen.print_entry()
    clock.advance(minutes=30)
    screen.on_scanned(entry.payload)
    due = screen.quote.amount_due
    assert screen.pay(PaymentMethod.CARD)  # sent to the terminal
    qtbot.waitUntil(lambda: not screen.pos_busy, timeout=5000)
    assert screen.terminal.requests == [due]
    assert screen.card_button.isEnabled()
    with hw_ctx.read() as session:
        payments = session.scalars(select(Payment)).all()
    if paid:
        assert screen.quote is None
        assert payments[0].method == "card"
        assert payments[0].reference and len(payments[0].reference) == 12
    else:
        assert screen.quote is not None  # still waiting for another way to pay
        assert payments == []


def test_led_rotation(screen, hw_ctx, qtbot):
    led = screen.led
    assert isinstance(led, SimulatorLed)
    shop = PeopleService(hw_ctx).create_shop("مبلمان آرتا")
    AdService(hw_ctx).create_ad(shop.id, "gold", MON, MON + timedelta(days=3), offer="۲۰٪ تخفیف")
    AdService(hw_ctx).create_ad(shop.id, "bronze", MON, MON, offer="فقط روی رسید")
    shown = [screen.led_tick() for _ in range(3)]
    assert any("جای خالی" in text for text in shown if text)
    assert any("مبلمان آرتا" in text for text in shown if text)
    assert not any("فقط روی رسید" in text for text in shown if text)
    qtbot.waitUntil(lambda: len(led.shown) == 3, timeout=5000)


def test_cards_box_in_subscriber_profile(qtbot, hw_ctx):
    from caspian_parking.app import build_main_window

    person = PeopleService(hw_ctx).create_person(PersonInput(first_name="رضا", plates=[(PLATE, None)]))
    window = build_main_window(hw_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("subscribers")
    profile = screen.profile
    with hw_ctx.read() as session:
        from caspian_parking.data.models import Person

        profile.show_person(session.get(Person, person.id))
    profile.cards.uid.setText("ab-12")
    assert profile.cards.add_card()
    assert profile.cards.rows.count() == 1
    card = CardService(hw_ctx).cards_of(person.id)[0]
    assert card.uid == "AB12"
    assert profile.cards.mark_lost(card.id, reason="گم شد")
    assert not CardService(hw_ctx).cards_of(person.id)[0].is_active


def test_device_settings_and_tests(qtbot, admin_ctx):
    from caspian_parking.config.machine import load_machine_config
    from caspian_parking.ui.screens.settings_devices import HardwareTab

    tab = HardwareTab(admin_ctx)
    qtbot.addWidget(tab)
    for combo, value in (
        (tab.barrier_kind, "simulator"),
        (tab.rfid_kind, "tcp"),
        (tab.pos_kind, "simulator"),
        (tab.led_kind, "simulator"),
    ):
        combo.setCurrentIndex(combo.findData(value))
    tab.rfid_port.setText("10.0.0.60:6000")
    assert tab.test_barrier()
    assert tab.test_led()
    assert tab.test_pos()
    tab.save()
    devices = load_machine_config(admin_ctx.data_root.config).devices
    assert (devices.barrier_kind, devices.rfid_kind, devices.rfid_port) == ("simulator", "tcp", "10.0.0.60:6000")
    tab.pos_kind.setCurrentIndex(tab.pos_kind.findData("manual"))
    assert not tab.test_pos()


def test_slideshow_shows_shop_slides(qtbot, hw_ctx, tmp_path):
    from caspian_parking.ui.widgets.slideshow import SlideshowWindow

    folder = tmp_path / "slides"
    folder.mkdir()
    window = SlideshowWindow(folder, 60, texts=lambda: ["مبلمان آرتا\n۲۰٪ تخفیف"])
    qtbot.addWidget(window)
    assert window.current_text == "مبلمان آرتا\n۲۰٪ تخفیف"  # no images: shop slides only
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(QColor(10, 20, 30))
    image.save(str(folder / "a.png"))
    window.rotation.rescan()
    assert window.advance().name == "a.png"  # last image of the round → shop slides queued
    assert window.advance() is None
    assert window.current_text == "مبلمان آرتا\n۲۰٪ تخفیف"
    assert window.advance().name == "a.png"
