from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select

from caspian_parking.core.barcode import decode_payload
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.tariff import VehicleType, VisitKind
from caspian_parking.core.tickets import parse_ticket_number
from caspian_parking.data.models import (
    ActiveSession,
    Adjustment,
    Cancellation,
    EntryEvent,
    ExitEvent,
    GateSequence,
    NightMark,
    Payment,
    Reprint,
    Visit,
)
from caspian_parking.data.session import AppendOnlyViolation
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.gate_service import AlreadyInside, GateError, GateService, PaymentMethod

MON = date(2026, 9, 28)
THU = date(2026, 10, 1)
PLATE = parse_plate("12ب345-22")
OTHER = parse_plate("55ج777-11")


def at(day: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm, ss)))


def login(ctx, preset: str, name: str | None = None) -> None:
    with ctx.uow() as session:
        user = auth.create_user(session, name or preset, preset, "secret1", preset=preset)
    ctx.user = CurrentUser.from_user(user)


@pytest.fixture
def gate(app_ctx, clock):
    clock.set(at(MON, 10))
    login(app_ctx, "supervisor")
    return GateService(app_ctx)


def count(ctx, model) -> int:
    with ctx.read() as session:
        return int(session.scalar(select(func.count()).select_from(model)))


# ---------------------------------------------------------------- entry


def test_entry_creates_session_event_and_signed_ticket(gate, app_ctx):
    result = gate.register_entry(PLATE)
    assert str(result.ticket) == "1-00001-" + str(result.ticket.check_digit)
    assert result.session.plate_key == PLATE.key
    assert result.session.category == "transient"
    payload = decode_payload(result.payload, gate.hmac_key())
    assert payload.gate == 1
    assert payload.sequence_mod == 1
    assert payload.entry_utc == at(MON, 10)
    assert count(app_ctx, ActiveSession) == 1
    assert count(app_ctx, EntryEvent) == 1
    second = gate.register_entry(OTHER)
    assert second.ticket.sequence == 2
    assert gate.inside_count() == 2


def test_one_open_session_per_plate(gate):
    first = gate.register_entry(PLATE)
    with pytest.raises(AlreadyInside) as info:
        gate.register_entry(PLATE)
    assert info.value.session.id == first.session.id


def test_no_plate_receipts_are_debounced(gate, clock):
    gate.register_entry(None)
    with pytest.raises(GateError, match="debounce"):
        gate.register_entry(None)
    clock.advance(seconds=4)
    assert gate.register_entry(None).session.no_plate


def test_categories(gate):
    assert gate.register_entry(PLATE, VehicleType.MOTORCYCLE).session.category == "motorcycle"
    assert gate.register_entry(OTHER, kind=VisitKind.PASS_THROUGH, pass_type="taxi").session.category == "pass_through"
    assert gate.register_entry(parse_plate("11د111-11"), VehicleType.VAN).session.category == "transient"


def test_sequence_never_repeats_even_if_sequence_row_is_lost(gate, app_ctx, clock):
    gate.register_entry(PLATE)
    gate.register_entry(OTHER)
    with app_ctx.uow() as session:
        session.execute(delete(GateSequence))  # e.g. restored from an old backup
    clock.advance(seconds=10)
    assert gate.register_entry(parse_plate("11د111-11")).ticket.sequence == 3


def test_operator_permission_required(app_ctx, clock):
    login(app_ctx, "operator")
    service = GateService(app_ctx)
    assert service.register_entry(PLATE).session is not None
    app_ctx.user = None
    with pytest.raises(GateError, match="permission"):
        service.register_entry(OTHER)


# ---------------------------------------------------------------- lookup


def test_resolve_by_barcode_ticket_number_and_plate(gate):
    result = gate.register_entry(PLATE)
    assert gate.resolve(result.payload).id == result.session.id
    assert gate.resolve(str(result.ticket)).id == result.session.id
    assert gate.resolve(str(result.ticket).replace("-", "")).id == result.session.id
    assert gate.resolve("۱۲ ب ۳۴۵ ایران ۲۲").id == result.session.id
    for bad in ("", "   ", "1-99999-" + str(parse_ticket_number(str(result.ticket)).check_digit)):
        with pytest.raises(GateError):
            gate.resolve(bad)


def test_forged_barcode_is_refused(gate):
    result = gate.register_entry(PLATE)
    forged = result.payload[:10] + ("1" if result.payload[10] != "1" else "2") + result.payload[11:]
    with pytest.raises(GateError, match="invalid"):
        gate.resolve(forged)


def test_barcode_of_exited_vehicle_is_not_inside(gate, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=5)
    gate.complete_exit(gate.quote(result.session.id), PaymentMethod.CASH)
    with pytest.raises(GateError, match="not_inside"):
        gate.resolve(result.payload)


def test_search_and_todays_entries(gate, clock):
    gate.register_entry(PLATE)
    gate.register_entry(OTHER)
    assert [s.plate_key for s in gate.search_inside("345")] == [PLATE.key]
    assert len(gate.search_inside()) == 2
    assert len(gate.todays_entries()) == 2
    clock.set(at(MON + timedelta(days=1), 10))
    assert gate.todays_entries() == []


# ---------------------------------------------------------------- exit & payment


def test_exit_with_cash(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=72)
    quote = gate.quote(result.session.id)
    assert quote.amount_due == 230_000
    visit = gate.complete_exit(quote, PaymentMethod.CASH)
    assert visit.status == "paid"
    assert visit.amount_paid == 230_000
    assert gate.inside_count() == 0
    with app_ctx.read() as session:
        payment = session.scalars(select(Payment)).one()
        assert (payment.method, payment.amount, payment.purpose) == ("cash", 230_000, "parking")
        exit_event = session.scalars(select(ExitEvent)).one()
        assert exit_event.breakdown["extra_minutes"] == 12
        assert session.scalars(select(Visit)).one().total_minutes == 72


@pytest.mark.parametrize("method", list(PaymentMethod))
def test_payment_methods_are_recorded_separately(gate, app_ctx, clock, method):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=30)
    gate.complete_exit(gate.quote(result.session.id), method)
    with app_ctx.read() as session:
        assert session.scalars(select(Payment)).one().method == method.value


def test_free_day_exit_needs_no_payment(gate, app_ctx, clock):
    clock.set(at(THU, 11))
    result = gate.register_entry(PLATE)
    clock.advance(hours=3)
    visit = gate.complete_exit(gate.quote(result.session.id), None)
    assert visit.status == "free"
    assert count(app_ctx, Payment) == 0


def test_payment_method_required_when_something_is_due(gate, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=5)
    with pytest.raises(GateError, match="method"):
        gate.complete_exit(gate.quote(result.session.id), None)


def test_manual_amount_needs_permission_and_reason(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=90)
    quote = gate.quote(result.session.id)
    with pytest.raises(GateError, match="reason"):
        gate.complete_exit(quote, PaymentMethod.CASH, manual_amount=100_000)
    visit = gate.complete_exit(quote, PaymentMethod.CASH, manual_amount=100_000, reason="تخفیف مدیریت")
    assert visit.amount_paid == 100_000
    with app_ctx.read() as session:
        adjustment = session.scalars(select(Adjustment)).one()
        assert (adjustment.kind, adjustment.amount_before, adjustment.amount_after) == (
            "manual_amount",
            290_000,
            100_000,
        )
        assert adjustment.reason == "تخفیف مدیریت"


def test_operator_cannot_change_amounts(app_ctx, clock):
    clock.set(at(MON, 10))
    login(app_ctx, "operator")
    service = GateService(app_ctx)
    result = service.register_entry(PLATE)
    clock.advance(minutes=30)
    with pytest.raises(GateError, match="permission"):
        service.complete_exit(service.quote(result.session.id), PaymentMethod.CASH, manual_amount=1, reason="x")


def test_night_fine_waiver(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.set(at(MON + timedelta(days=1), 10))
    quote = gate.quote(result.session.id)
    assert quote.breakdown.night_fines == 2_000_000
    with pytest.raises(GateError, match="bad_amount"):
        gate.complete_exit(quote, PaymentMethod.CARD, night_fines=3_000_000, reason="x")
    visit = gate.complete_exit(quote, PaymentMethod.CARD, night_fines=0, reason="نگهبان مجموعه")
    assert visit.amount_paid == quote.breakdown.transient_fee
    with app_ctx.read() as session:
        assert session.scalars(select(Adjustment)).one().kind == "night_fine"


def test_lost_ticket_flag(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=10)
    visit = gate.complete_exit(gate.quote(result.session.id, lost_ticket=True), PaymentMethod.CASH)
    assert visit.lost_ticket
    assert "lost_ticket" in visit.flags


# ---------------------------------------------------------------- fleeing & debts


def test_fleeing_creates_debt_and_alert_on_next_arrival(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=120)
    debt = gate.flee(gate.quote(result.session.id))
    assert debt.amount == 380_000
    clock.set(at(MON + timedelta(days=2), 11))
    again = gate.register_entry(PLATE)
    assert [d.remaining for d in again.debts] == [380_000]
    gate.collect_debt(debt.id, PaymentMethod.CASH, amount=80_000)
    assert [d.remaining for d in gate.open_debts(PLATE)] == [300_000]
    with pytest.raises(GateError, match="bad_amount"):
        gate.collect_debt(debt.id, PaymentMethod.CASH, amount=999_999_999)
    gate.collect_debt(debt.id, PaymentMethod.CARD)
    assert gate.open_debts(PLATE) == []
    with pytest.raises(GateError, match="settled"):
        gate.collect_debt(debt.id, PaymentMethod.CASH)
    with app_ctx.read() as session:
        visit = session.scalar(select(Visit).where(Visit.session_id == result.session.id))
        assert visit.status == "recovered"
        assert visit.amount_paid == 380_000
    with pytest.raises(GateError, match="debt_not_found"):
        gate.collect_debt("missing", PaymentMethod.CASH)


# ---------------------------------------------------------------- cancellations


def test_cancel_entry_keeps_history(gate, app_ctx):
    result = gate.register_entry(PLATE)
    with pytest.raises(GateError, match="reason"):
        gate.cancel_entry(result.session.id, " ")
    gate.cancel_entry(result.session.id, "چاپ اشتباه")
    assert gate.inside_count() == 0
    assert count(app_ctx, EntryEvent) == 1
    with app_ctx.read() as session:
        assert session.scalars(select(Cancellation)).one().reason == "چاپ اشتباه"
        assert session.scalars(select(Visit)).one().status == "cancelled"
    assert gate.counters()["total"] == 1  # immutable counter never goes down


def test_operator_cannot_cancel(app_ctx, clock):
    login(app_ctx, "operator")
    service = GateService(app_ctx)
    result = service.register_entry(PLATE)
    with pytest.raises(GateError, match="permission"):
        service.cancel_entry(result.session.id, "x")


def test_cancel_and_correct_payment(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=30)
    gate.complete_exit(gate.quote(result.session.id), PaymentMethod.CASH)
    with app_ctx.read() as session:
        payment = session.scalars(select(Payment)).one()
    gate.cancel_payment(payment.id, "کارت بود نه نقد")
    with pytest.raises(GateError, match="already"):
        gate.cancel_payment(payment.id, "again")
    gate.record_payment(result.session.id, PaymentMethod.CARD, 190_000, "اصلاح")
    with app_ctx.read() as session:
        visit = session.scalars(select(Visit)).one()
        assert visit.amount_paid == 190_000
        assert "payment_cancelled" in visit.flags
        assert count(app_ctx, Payment) == 2


def test_events_are_append_only(gate, app_ctx, clock):
    result = gate.register_entry(PLATE)
    clock.advance(minutes=30)
    gate.complete_exit(gate.quote(result.session.id), PaymentMethod.CASH)
    with pytest.raises(AppendOnlyViolation), app_ctx.uow() as session:
        session.scalars(select(Payment)).one().amount = 1
        session.flush()


# ---------------------------------------------------------------- duplicates & night parking


def test_duplicate_reprint(gate, app_ctx):
    result = gate.register_entry(PLATE)
    active = gate.reprint_duplicate(result.session.id)
    assert active.duplicate_count == 1
    assert count(app_ctx, Reprint) == 1


def test_night_marks_and_auto_flag(gate, app_ctx, clock):
    marked = gate.register_entry(PLATE)
    gate.register_entry(OTHER)
    gate.mark_night(marked.session.id)
    clock.set(at(MON + timedelta(days=1), 9, 45))
    flagged = gate.auto_flag_overnight()
    assert len(flagged) == 1  # only OTHER: PLATE already has a mark for Monday night
    assert gate.auto_flag_overnight() == []  # idempotent
    with app_ctx.read() as session:
        autos = session.scalars(select(NightMark).where(NightMark.auto.is_(True))).all()
        assert len(autos) == 1
    assert len(gate.night_list()) == 2


def test_auto_flag_after_closing_same_evening(gate, clock):
    gate.register_entry(PLATE)
    clock.set(at(MON, 21))
    assert len(gate.auto_flag_overnight()) == 1


# ---------------------------------------------------------------- occupancy & counters


def test_occupancy_fills_open_levels(gate, app_ctx):
    for index in range(3):
        gate.register_entry(parse_plate(f"1{index}ب345-22"))
    levels = gate.occupancy()
    assert [(lv.code, lv.capacity, lv.inside) for lv in levels] == [("P1", 202, 3)]


def test_counters_by_category(gate, clock):
    gate.register_entry(PLATE)
    gate.register_entry(OTHER, VehicleType.MOTORCYCLE)
    gate.register_entry(parse_plate("11د111-11"), kind=VisitKind.PASS_THROUGH)
    counters = gate.counters()
    assert counters["transient"] == 1
    assert counters["motorcycle"] == 1
    assert counters["pass_through"] == 1
    assert counters["total"] == 3
    assert gate.counters(since=at(MON, 11))["total"] == 0


def test_supervisor_has_needed_permissions():
    from caspian_parking.core.permissions import SUPERVISOR_PERMISSIONS

    assert Permission.CANCEL_TRANSACTIONS in SUPERVISOR_PERMISSIONS


@pytest.mark.serial
def test_entry_and_quote_are_fast(gate, clock):
    started = _time.perf_counter()
    result = gate.register_entry(PLATE)
    entry_ms = (_time.perf_counter() - started) * 1000
    clock.advance(minutes=200)
    started = _time.perf_counter()
    gate.quote(result.session.id)
    quote_ms = (_time.perf_counter() - started) * 1000
    assert entry_ms < 1000
    assert quote_ms < 100
