"""Subscribers, wallets, free access, blocklist and their effect at the gate (SPEC §4.6–4.9)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.subscriptions import EntryStatus, Light
from caspian_parking.data.models import BlockAttempt, SubscriptionPayment, WalletTransaction
from caspian_parking.data.repositories.system import AuditRepository
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.blocklist import BlockError, BlocklistService
from caspian_parking.services.gate_service import Blocked, GateService
from caspian_parking.services.identification import Kind
from caspian_parking.services.people import PeopleError, PeopleService, PersonInput, normalize_mobile

MON = date(2026, 9, 28)
PLATE = parse_plate("12ب345-22")
PLATE2 = parse_plate("55ج777-11")
DAY = timedelta(days=1)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


def login(ctx, preset: str) -> None:
    with ctx.uow() as session:
        user = auth.create_user(session, preset + "x", preset, "secret1", preset=preset)
    ctx.user = CurrentUser.from_user(user)


@pytest.fixture
def svc(app_ctx, clock):
    clock.set(at(MON, 10))
    login(app_ctx, "admin")
    return PeopleService(app_ctx)


def subscriber(svc, *plates, **overrides):
    data = PersonInput(first_name="علی", last_name="رضایی", mobile="09121234567", plates=[(p, None) for p in plates])
    for key, value in overrides.items():
        setattr(data, key, value)
    return svc.create_person(data)


# ---------------------------------------------------------------- people


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("09121234567", "09121234567"),
        ("۰۹۱۲۱۲۳۴۵۶۷", "09121234567"),
        ("+98 912 123 4567", "09121234567"),
        ("9121234567", "09121234567"),
        ("", None),
    ],
)
def test_mobile_normalization(raw, expected):
    assert normalize_mobile(raw) == expected


def test_bad_mobile():
    with pytest.raises(PeopleError, match="mobile"):
        normalize_mobile("0912")


def test_create_subscriber_with_plates_and_search(svc, app_ctx):
    person = subscriber(svc, PLATE, PLATE2)
    assert person.full_name == "علی رضایی"
    assert len(svc.plates_of(person.id)) == 2
    assert [p.id for p in svc.search("345")] == [person.id]
    assert [p.id for p in svc.search("علی")] == [person.id]
    assert svc.search("0912123")[0].id == person.id
    with pytest.raises(PeopleError, match="plate_taken"):
        subscriber(svc, PLATE)
    with pytest.raises(PeopleError, match="name_required"):
        svc.create_person(PersonInput(first_name=" "))
    with app_ctx.read() as session:
        assert AuditRepository(session).for_record("people", person.id)


def test_plates_can_be_added_and_removed(svc):
    person = subscriber(svc, PLATE)
    row = svc.add_plate(person.id, PLATE2, "ماشین دوم")
    assert svc.add_plate(person.id, PLATE2).id == row.id
    svc.remove_plate(row.id, "فروخته شد")
    assert [p.plate_key for p in svc.plates_of(person.id)] == [PLATE.key]
    again = svc.add_plate(person.id, PLATE2)
    assert again.id == row.id
    assert again.is_active


def test_dedicated_fields_cannot_be_updated_directly(svc):
    person = subscriber(svc, PLATE)
    with pytest.raises(PeopleError, match="dedicated"):
        svc.update_person(person.id, subscription_end_utc=at(MON, 10))
    updated = svc.update_person(person.id, vehicle_model="پژو ۲۰۶", mobile="09350000000")
    assert updated.vehicle_model == "پژو ۲۰۶"


# ---------------------------------------------------------------- subscriptions


def test_first_payment_and_light(svc, clock):
    person = subscriber(svc, PLATE)
    preview = svc.preview_payment(person.id)
    assert preview.amount == 4_000_000
    assert preview.days == 30
    payment = svc.pay_subscription(person.id, "cash")
    assert payment.new_end_utc == at(MON, 10) + 30 * DAY
    state = svc.state(svc.get(person.id))
    assert state.light is Light.GREEN
    assert state.days_left == 30
    clock.advance(days=26)
    assert svc.state(svc.get(person.id)).light is Light.AMBER
    clock.advance(days=3)
    assert svc.state(svc.get(person.id)).light is Light.RED
    clock.advance(days=2)
    assert svc.state(svc.get(person.id)).light is Light.BLACK


def test_early_payment_and_price_override(svc, clock):
    person = subscriber(svc, PLATE, price_override=3_500_000)
    svc.pay_subscription(person.id, "card")
    clock.advance(days=20)
    payment = svc.pay_subscription(person.id, "cash")
    assert payment.amount == 3_500_000
    assert payment.new_end_utc == at(MON, 10) + 60 * DAY


def test_negative_subscription_end_to_end(svc, app_ctx, clock):
    person = subscriber(svc, PLATE)
    svc.pay_subscription(person.id, "cash")
    gate = GateService(app_ctx)
    clock.set(at(MON, 10) + 31 * DAY)  # expired one day ago
    ident = gate.identify(PLATE)
    assert ident.status is EntryStatus.EXPIRED
    assert not ident.covered
    with pytest.raises(PeopleError, match="reason"):
        svc.allow_negative(person.id, 10, " ")
    svc.allow_negative(person.id, 10, "قول پرداخت داد")
    # enters on three different days (twice on one of them)
    for offset_hours in (0, 3, 24, 72):
        clock.set(at(MON, 10) + 31 * DAY + timedelta(hours=offset_hours))
        result = gate.register_entry(PLATE)
        assert result.session.category == "subscriber"
        assert "negative_subscription" in result.session.flags
        assert not result.needs_receipt
        clock.advance(minutes=30)
        gate.complete_exit(gate.quote(result.session.id), None)
    clock.set(at(MON, 10) + 36 * DAY)
    preview = svc.preview_payment(person.id)
    assert preview.renewal.deducted_days == 3
    payment = svc.pay_subscription(person.id, "cash")
    assert payment.new_end_utc == clock.now_utc() + 27 * DAY
    assert len(payment.used_days) == 3
    assert not svc.get(person.id).negative_allowed  # consumed by the payment


def test_negative_limit_expires(svc, app_ctx, clock):
    person = subscriber(svc, PLATE)
    svc.pay_subscription(person.id, "cash")
    svc.allow_negative(person.id, 2, "ok")
    clock.set(at(MON, 10) + 33 * DAY)  # 3 days overdue > 2
    assert GateService(app_ctx).identify(PLATE).status is EntryStatus.EXPIRED


def test_subscriber_exit_is_free_except_night_fines(svc, app_ctx, clock):
    person = subscriber(svc, PLATE)
    svc.pay_subscription(person.id, "cash")
    gate = GateService(app_ctx)
    result = gate.register_entry(PLATE)
    assert result.identification.kind is Kind.SUBSCRIBER
    clock.advance(hours=5)
    assert gate.quote(result.session.id).amount_due == 0
    clock.set(at(MON + DAY, 10))
    quote = gate.quote(result.session.id)
    assert quote.breakdown.transient_fee == 0
    assert quote.amount_due == 2_000_000
    svc.set_night_exempt(person.id, True, "نگهبان")
    assert gate.quote(result.session.id).amount_due == 0


def test_expired_subscriber_is_a_transient(svc, app_ctx, clock):
    person = subscriber(svc, PLATE)
    svc.pay_subscription(person.id, "cash")
    clock.advance(days=40)
    result = GateService(app_ctx).register_entry(PLATE)
    assert result.session.category == "transient"
    assert "expired_subscription" in result.session.flags
    assert result.needs_receipt
    assert result.session.person_id == person.id


def test_concurrency_second_plate_is_transient(svc, app_ctx, clock):
    person = subscriber(svc, PLATE, PLATE2)
    svc.pay_subscription(person.id, "cash")
    gate = GateService(app_ctx)
    assert gate.register_entry(PLATE).session.category == "subscriber"
    second = gate.register_entry(PLATE2)
    assert second.session.category == "transient"
    assert "concurrency_exceeded" in second.session.flags
    assert second.identification.concurrency_exceeded


# ---------------------------------------------------------------- shops & wallets


def test_wallet_deposit_debit_statement_and_low_balance(svc, app_ctx):
    shop = svc.create_shop("مبلمان آرتا", unit="همکف ۱۲", low_balance_threshold=1_000_000)
    member = subscriber(svc, PLATE, shop_id=shop.id, payer="shop")
    assert svc.low_balance(shop)
    svc.deposit(shop.id, 10_000_000, "card", "واریز مهر")
    assert not svc.low_balance(shop)
    payment = svc.pay_subscription(member.id, "wallet")
    assert payment.shop_id == shop.id
    assert svc.balance(shop.id) == 6_000_000
    rows = svc.statement(shop.id)
    assert [(r.kind, r.amount, running) for r, running in rows] == [
        ("deposit", 10_000_000, 10_000_000),
        ("subscription", -4_000_000, 6_000_000),
    ]
    assert [m.id for m in svc.members(shop.id)] == [member.id]
    with pytest.raises(PeopleError, match="bad_amount"):
        svc.deposit(shop.id, 0, "cash")
    with pytest.raises(PeopleError, match="no_shop"):
        svc.pay_subscription(subscriber(svc, PLATE2).id, "wallet")
    assert [s.name for s in svc.shops("آرتا")] == ["مبلمان آرتا"]
    svc.update_shop(shop.id, phone="02144000000")


def test_auto_renew_from_wallet(svc, app_ctx, clock):
    shop = svc.create_shop("مغازه")
    rich = subscriber(svc, PLATE, shop_id=shop.id, payer="shop")
    svc.deposit(shop.id, 5_000_000, "cash")
    renewed = svc.renew_due_from_wallets()
    assert [p.person_id for p in renewed] == [rich.id]
    assert svc.renew_due_from_wallets() == []  # not due any more
    clock.advance(days=31)
    assert svc.renew_due_from_wallets() == []  # only 1,000,000 left
    with app_ctx.read() as session:
        assert len(session.scalars(select(WalletTransaction)).all()) == 2
        assert len(session.scalars(select(SubscriptionPayment)).all()) == 1


# ---------------------------------------------------------------- free access


def test_staff_and_guest_free_access(svc, app_ctx, clock):
    staff = svc.create_person(
        PersonInput(first_name="نگهبان", kind="free", free_category="staff", plates=[(PLATE, None)])
    )
    assert staff.approved_by == app_ctx.user.id
    guest = svc.create_person(
        PersonInput(first_name="مهمان", kind="free", free_category="guest", plates=[(PLATE2, None)])
    )
    gate = GateService(app_ctx)
    staff_entry = gate.register_entry(PLATE)
    assert staff_entry.session.category == "free"
    guest_ident = gate.identify(PLATE2)
    assert guest_ident.kind is Kind.FREE
    assert not guest_ident.covered  # no permit yet
    svc.add_guest_permit(guest.id, MON, MON + DAY, "جلسه")
    assert gate.identify(PLATE2).covered
    clock.set(at(MON + 2 * DAY, 10))
    assert not gate.identify(PLATE2).covered  # permit expired
    with pytest.raises(PeopleError, match="bad_range"):
        svc.add_guest_permit(guest.id, MON, MON - DAY)
    subscriber_person = subscriber(svc)
    with pytest.raises(PeopleError, match="not_free"):
        svc.add_guest_permit(subscriber_person.id, MON, MON)
    assert len(svc.permits_of(guest.id)) == 1


# ---------------------------------------------------------------- blocklist


def test_blocked_plate_is_refused_and_logged(svc, app_ctx):
    blocks = BlocklistService(app_ctx)
    block = blocks.block_plate(PLATE, "debtor", "بدهی قدیمی")
    gate = GateService(app_ctx)
    with pytest.raises(Blocked) as info:
        gate.register_entry(PLATE)
    assert info.value.match.block.id == block.id
    assert not info.value.match.generic  # admin may see details
    with app_ctx.read() as session:
        assert len(session.scalars(select(BlockAttempt)).all()) == 1
    assert len(blocks.attempts(block.id)) == 1
    with pytest.raises(BlockError, match="reason"):
        blocks.unblock(block.id, "")
    blocks.unblock(block.id, "تسویه شد")
    assert gate.register_entry(PLATE).session is not None


def test_person_block_covers_all_their_plates_and_generic_message(svc, app_ctx):
    person = subscriber(svc, PLATE, PLATE2)
    BlocklistService(app_ctx).block_person(person.id, "security", "مورد امنیتی")
    login(app_ctx, "operator")
    match = BlocklistService(app_ctx).match
    with app_ctx.read() as session:
        result = match(session, PLATE2.key)
    assert result is not None
    assert result.generic  # operators only see the generic message
    with pytest.raises(BlockError, match="permission"):
        BlocklistService(app_ctx).block_plate(PLATE, "other", "x")


def test_block_validation(svc, app_ctx):
    blocks = BlocklistService(app_ctx)
    with pytest.raises(BlockError, match="category"):
        blocks.block_plate(PLATE, "whole-shop", "x")
    with pytest.raises(BlockError, match="not_found"):
        blocks.unblock("missing", "x")
    assert blocks.active() == []


def test_debt_shows_in_identification(svc, app_ctx, clock):
    gate = GateService(app_ctx)
    result = gate.register_entry(PLATE)
    clock.advance(minutes=30)
    gate.flee(gate.quote(result.session.id))
    assert gate.identify(PLATE).debt_total == 190_000
