from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.tariff import DayHours, PriceBasis, TariffError, VehicleType
from caspian_parking.data.repositories.system import AuditRepository
from caspian_parking.data.repositories.tariff import HolidayRepository, TariffVersionRepository
from caspian_parking.services import tariff_service as ts
from tests.support.tariff import seed_values

MON = date(2026, 9, 28)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


def test_seeded_tariff_matches_spec(app_ctx):
    with app_ctx.read() as session:
        context = ts.load_context(session)
    assert context.basis is PriceBasis.ENTRY
    assert context.schedule.at(at(MON, 10)) == seed_values()
    assert context.calendar.is_free_day(MON + timedelta(days=3))  # Thursday
    assert context.quote(at(MON, 10), at(MON, 11, 13)).total == 240_000
    assert context.quote(at(MON, 10), at(MON, 10, 5), vehicle=VehicleType.MOTORCYCLE).total == 200_000


def test_new_tariff_version_and_history(app_ctx, clock):
    clock.set(at(MON, 9))
    with app_ctx.uow() as session:
        record = ts.add_tariff_version(
            session, seed_values(entry_fee=250_000), at(MON, 12), clock.now_utc(), note="افزایش مهر"
        )
        with pytest.raises(TariffError, match="retroactive"):
            ts.add_tariff_version(session, seed_values(), at(MON, 8), clock.now_utc())
    with app_ctx.read() as session:
        context = ts.load_context(session)
        history = TariffVersionRepository(session).history()
        assert [r.note for r in history] == ["افزایش مهر", "seed"]
        assert AuditRepository(session).for_record("tariff_versions", record.id)
    assert context.quote(at(MON, 11), at(MON, 11, 30)).total == 190_000
    assert context.quote(at(MON, 12), at(MON, 12, 30)).total == 250_000


def test_future_version_can_be_withdrawn_but_not_past_ones(app_ctx, clock):
    clock.set(at(MON, 9))
    with app_ctx.uow() as session:
        future = ts.add_tariff_version(session, seed_values(entry_fee=1), at(MON, 15), clock.now_utc())
        ts.cancel_tariff_version(session, future, clock.now_utc(), reason="اشتباه")
        seed = TariffVersionRepository(session).history()[-1]
        with pytest.raises(TariffError, match="already_effective"):
            ts.cancel_tariff_version(session, seed, clock.now_utc(), reason="x")
    with app_ctx.read() as session:
        assert ts.load_schedule(session).at(at(MON, 16)).entry_fee == 190_000


def test_calendar_and_basis_settings(app_ctx):
    hours = {d: DayHours(time(10), time(22)) for d in range(7)}
    hours[4] = None  # Friday closed
    with app_ctx.uow() as session:
        ts.save_calendar(session, hours, {4})
        ts.save_price_basis(session, PriceBasis.EXIT)
    with app_ctx.read() as session:
        calendar = ts.load_calendar(session)
        assert ts.load_price_basis(session) is PriceBasis.EXIT
    assert calendar.hours_for(MON) == DayHours(time(10), time(22))
    assert calendar.hours_for(MON + timedelta(days=4)) is None
    assert calendar.free_weekdays == frozenset({4})


def test_holidays(app_ctx):
    with app_ctx.uow() as session:
        holiday = ts.add_holiday(session, MON, "تعطیل رسمی")
        assert ts.add_holiday(session, MON, "تعطیل رسمی").id == holiday.id
    with app_ctx.read() as session:
        context = ts.load_context(session)
    assert context.quote(at(MON, 10), at(MON, 12)).total == 0
    with app_ctx.uow() as session:
        ts.remove_holiday(session, session.get(type(holiday), holiday.id), reason="اشتباه ثبت شد")
    with app_ctx.read() as session:
        assert ts.load_context(session).quote(at(MON, 10), at(MON, 12)).total == 380_000
        assert HolidayRepository(session).upcoming() == []
    with app_ctx.uow() as session:
        again = ts.add_holiday(session, MON, "عنوان جدید")
        assert again.is_active
        assert HolidayRepository(session).upcoming(since=MON)[0].title == "عنوان جدید"


def test_schedule_falls_back_to_seed_when_all_versions_inactive(app_ctx):
    with app_ctx.uow() as session:
        for record in TariffVersionRepository(session).history():
            TariffVersionRepository(session).deactivate(record, "test")
    with app_ctx.read() as session:
        assert ts.load_schedule(session).at(datetime(2030, 1, 1, tzinfo=UTC)) == seed_values()
