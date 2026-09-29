"""Exhaustive tariff tests (SPEC §4.5). Times are Tehran wall clock unless stated."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.tariff import (
    DayHours,
    ParkingCalendar,
    PriceBasis,
    TariffError,
    TariffSchedule,
    TariffValues,
    TariffVersion,
    VehicleType,
    VisitKind,
    compute_price,
)
from caspian_parking.core.tariff.engine import (
    FLAG_AFTER_HOURS_ARRIVAL,
    FLAG_COUPON,
    FLAG_INSIDE_AT_OPENING,
    FLAG_NIGHT_EXEMPT,
    FLAG_NO_CHARGEABLE_TIME,
    FLAG_OVERNIGHT,
    FLAG_PASS_THROUGH_FREE,
    FLAG_PASS_THROUGH_OVER_LIMIT,
    car_fee,
    chargeable_minutes,
    is_after_hours,
    nights_crossed,
)
from tests.support.tariff import seed_calendar, seed_values

# 2026-09-28 is a Monday; 09-30 Wednesday; 10-01 Thursday (free); 10-02 Friday (free).
MON, TUE, WED, THU, FRI = (date(2026, 9, 28) + timedelta(days=i) for i in range(5))
CAL = seed_calendar()
SCHEDULE = TariffSchedule.single(seed_values())


def at(day: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm, ss)))


def price(entry, exit_, **kwargs):
    kwargs.setdefault("calendar", CAL)
    kwargs.setdefault("schedule", SCHEDULE)
    return compute_price(entry, exit_, **kwargs)


# ---------------------------------------------------------------- SPEC §4.5 examples


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(5, 190_000), (60, 190_000), (61, 200_000), (72, 230_000), (73, 240_000), (120, 380_000)],
)
def test_spec_examples(minutes, expected):
    entry = at(MON, 10)
    result = price(entry, entry + timedelta(minutes=minutes))
    assert result.total == expected
    assert result.transient_fee == expected
    assert result.nights == 0
    assert result.total_minutes == minutes


def test_spec_example_wednesday_to_thursday():
    result = price(at(WED, 14), at(THU, 11))
    # Wednesday 14:00–20:30 = 390 chargeable minutes; Thursday is free.
    assert result.chargeable_minutes == 390
    assert result.extra_minutes == 330
    assert result.extra_amount == 1_045_000
    assert result.transient_fee == 1_240_000
    assert result.nights == 1
    assert result.night_fines == 2_000_000
    assert result.total == 3_240_000


def test_extra_formula_details():
    # 61 min → extra = ceil(190000 × 1 / 60) = 3167 → 193167 → rounded up to 200000
    assert car_fee(61, seed_values()) == (190_000, 1, 3_167, 6_833, 200_000)
    assert car_fee(0, seed_values()) == (0, 0, 0, 0, 0)


# ---------------------------------------------------------------- minute flooring


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), 0),
        (timedelta(seconds=59), 0),
        (timedelta(minutes=1), 190_000),
        (timedelta(minutes=60, seconds=59), 190_000),
        (timedelta(minutes=61), 200_000),
        (timedelta(minutes=61, seconds=59), 200_000),
    ],
)
def test_partial_minutes_are_never_charged(delta, expected):
    entry = at(MON, 12)
    assert price(entry, entry + delta).total == expected


def test_zero_length_stay():
    result = price(at(MON, 12), at(MON, 12))
    assert result.total == 0
    assert FLAG_NO_CHARGEABLE_TIME in result.flags


def test_exit_before_entry_is_rejected():
    with pytest.raises(TariffError):
        price(at(MON, 12), at(MON, 11))


# ---------------------------------------------------------------- free days, holidays, hours


@pytest.mark.parametrize("day", [THU, FRI])
def test_free_weekdays_cost_nothing(day):
    result = price(at(day, 10), at(day, 18))
    assert result.total == 0
    assert result.chargeable_minutes == 0


def test_holiday_is_free():
    calendar = seed_calendar(holidays=frozenset({MON}))
    assert price(at(MON, 10), at(MON, 15), calendar=calendar).total == 0
    assert price(at(TUE, 10), at(TUE, 11), calendar=calendar).total == 190_000


def test_custom_free_weekdays():
    calendar = seed_calendar(free_weekdays=frozenset())
    assert price(at(THU, 10), at(THU, 11), calendar=calendar).total == 190_000


def test_only_opening_hours_are_chargeable():
    # 08:00 → 10:00: only 09:30–10:00 is inside opening hours
    result = price(at(MON, 8), at(MON, 10))
    assert result.chargeable_minutes == 30
    assert result.transient_fee == 190_000
    assert FLAG_AFTER_HOURS_ARRIVAL in result.flags
    assert FLAG_INSIDE_AT_OPENING in result.flags


def test_stay_spanning_paid_free_paid_days():
    # Wednesday 20:00 → Saturday 10:00 : Wed 30 min + Sat 30 min chargeable, 3 closings crossed (Wed, Thu, Fri)
    saturday = FRI + timedelta(days=1)
    result = price(at(WED, 20), at(saturday, 10))
    assert result.chargeable_minutes == 60
    assert result.transient_fee == 190_000
    assert result.nights == 3
    assert result.total == 190_000 + 3 * 2_000_000


def test_multi_day_paid_stay():
    result = price(at(MON, 10), at(WED, 10))
    assert result.chargeable_minutes == 630 + 660 + 30
    assert result.extra_minutes == 1_260
    assert result.transient_fee == 4_180_000
    assert result.nights == 2
    assert result.total == 4_180_000 + 4_000_000


def test_closed_weekday_has_no_hours_and_no_fine():
    hours = dict(CAL.hours)
    hours[TUE.weekday()] = None
    calendar = seed_calendar(hours=hours, free_weekdays=frozenset())
    result = price(at(MON, 20), at(WED, 9), calendar=calendar)
    assert result.chargeable_minutes == 30  # Monday 20:00–20:30 only
    assert result.nights == 1  # Monday closing; Tuesday has no closing time


def test_custom_hours_per_weekday():
    hours = dict(CAL.hours)
    hours[MON.weekday()] = DayHours(time(10, 0), time(22, 0))
    calendar = seed_calendar(hours=hours)
    assert price(at(MON, 21), at(MON, 21, 50), calendar=calendar).nights == 0
    assert price(at(MON, 9), at(MON, 11), calendar=calendar).chargeable_minutes == 60


# ---------------------------------------------------------------- night fines


@pytest.mark.parametrize(
    ("exit_", "nights"),
    [((20, 30, 0), 0), ((20, 30, 1), 1), ((20, 31, 0), 1), ((23, 59, 0), 1)],
)
def test_night_fine_boundary_no_grace(exit_, nights):
    assert price(at(MON, 20), at(MON, *exit_)).nights == nights


@pytest.mark.parametrize(("exit_", "nights"), [((20, 59), 0), ((21, 0), 0), ((21, 1), 1)])
def test_night_fine_grace(exit_, nights):
    schedule = TariffSchedule.single(seed_values(night_fine_grace_minutes=30))
    assert price(at(MON, 20), at(MON, *exit_), schedule=schedule).nights == nights


def test_night_fine_applies_on_free_days():
    result = price(at(THU, 10), at(FRI, 10))
    assert result.transient_fee == 0
    assert result.nights == 1
    assert result.total == 2_000_000


def test_several_nights():
    result = price(at(MON, 12), at(MON, 12) + timedelta(days=5))
    assert result.nights == 5
    assert result.night_fines == 10_000_000
    assert FLAG_OVERNIGHT in result.flags


def test_night_exemption_keeps_the_count_but_not_the_amount():
    result = price(at(MON, 12), at(TUE, 10), night_exempt=True)
    assert result.nights == 1
    assert result.night_fines == 0
    assert FLAG_NIGHT_EXEMPT in result.flags


def test_after_hours_arrival_is_a_security_record():
    result = price(at(MON, 21), at(MON, 22))
    assert result.total == 0
    assert result.nights == 0
    assert FLAG_AFTER_HOURS_ARRIVAL in result.flags
    assert FLAG_INSIDE_AT_OPENING not in result.flags


def test_after_hours_arrival_still_inside_at_opening_is_flagged_and_priced():
    result = price(at(MON, 22), at(TUE, 10))
    assert FLAG_INSIDE_AT_OPENING in result.flags
    assert result.chargeable_minutes == 30
    assert result.nights == 0  # entered after Monday's closing
    assert result.total == 190_000


# ---------------------------------------------------------------- motorcycles


def test_motorcycle_flat_fee():
    for minutes in (1, 5, 60, 300):
        entry = at(MON, 10)
        result = price(entry, entry + timedelta(minutes=minutes), vehicle=VehicleType.MOTORCYCLE)
        assert result.total == 200_000
        assert result.extra_minutes == 0


def test_motorcycle_free_day_and_night():
    assert price(at(THU, 10), at(THU, 12), vehicle=VehicleType.MOTORCYCLE).total == 0
    overnight = price(at(MON, 10), at(TUE, 10), vehicle=VehicleType.MOTORCYCLE)
    assert overnight.total == 200_000 + 2_000_000
    no_fine = TariffSchedule.single(seed_values(motorcycle_night_fine=False))
    assert price(at(MON, 10), at(TUE, 10), vehicle=VehicleType.MOTORCYCLE, schedule=no_fine).total == 200_000


@pytest.mark.parametrize("vehicle", [VehicleType.SEDAN, VehicleType.VAN, VehicleType.TRUCK, VehicleType.OTHER])
def test_non_motorcycles_use_car_tariff(vehicle):
    assert price(at(MON, 10), at(MON, 11, 13), vehicle=vehicle).total == 240_000


# ---------------------------------------------------------------- pass-through


@pytest.mark.parametrize(
    ("minutes", "total", "flag"),
    [
        (0, 0, FLAG_PASS_THROUGH_FREE),
        (20, 0, FLAG_PASS_THROUGH_FREE),
        (21, 190_000, FLAG_PASS_THROUGH_OVER_LIMIT),
        (75, 240_000, FLAG_PASS_THROUGH_OVER_LIMIT),  # 190000 + ceil(190000×15/60)=47500 → 240000
    ],
)
def test_pass_through(minutes, total, flag):
    entry = at(MON, 11)
    result = price(entry, entry + timedelta(minutes=minutes), kind=VisitKind.PASS_THROUGH)
    assert result.total == total
    assert flag in result.flags


def test_pass_through_limit_is_configurable_and_uses_total_duration():
    schedule = TariffSchedule.single(seed_values(pass_through_free_minutes=45))
    entry = at(MON, 11)
    assert price(entry, entry + timedelta(minutes=45), kind=VisitKind.PASS_THROUGH, schedule=schedule).total == 0
    # 20:20 → 20:39 is free for a pass-through even though it crosses closing? No: the night fine still applies.
    late = price(at(MON, 20, 20), at(MON, 20, 39), kind=VisitKind.PASS_THROUGH)
    assert late.transient_fee == 0
    assert late.nights == 1


def test_pass_through_motorcycle_over_limit():
    entry = at(MON, 11)
    result = price(entry, entry + timedelta(minutes=30), kind=VisitKind.PASS_THROUGH, vehicle=VehicleType.MOTORCYCLE)
    assert result.total == 200_000


# ---------------------------------------------------------------- coupons


def test_coupon_zeroes_fee_but_not_fines():
    result = price(at(MON, 10), at(MON, 12), coupon=True)
    assert result.total == 0
    assert result.coupon_discount == 380_000
    assert result.fee_before_coupon == 380_000
    assert FLAG_COUPON in result.flags
    overnight = price(at(MON, 10), at(TUE, 10), coupon=True)
    assert overnight.transient_fee == 0
    assert overnight.total == 2_000_000


def test_coupon_on_free_stay_is_not_used():
    result = price(at(THU, 10), at(THU, 11), coupon=True)
    assert result.coupon_discount == 0
    assert FLAG_COUPON not in result.flags


# ---------------------------------------------------------------- tariff versions


def _two_versions():
    old = TariffVersion(datetime(2020, 1, 1, tzinfo=UTC), seed_values())
    new = TariffVersion(at(MON, 10, 30), seed_values(entry_fee=250_000, hourly_rate=250_000))
    return TariffSchedule((new, old))


def test_price_by_entry_time_tariff_default():
    schedule = _two_versions()
    result = price(at(MON, 10), at(MON, 11), schedule=schedule)
    assert result.total == 190_000
    assert result.tariff_effective_from == datetime(2020, 1, 1, tzinfo=UTC)


def test_price_by_exit_time_tariff():
    schedule = _two_versions()
    result = price(at(MON, 10), at(MON, 11), schedule=schedule, basis=PriceBasis.EXIT)
    assert result.total == 250_000
    assert result.tariff_effective_from == at(MON, 10, 30)


def test_schedule_lookup():
    schedule = _two_versions()
    assert schedule.at(at(MON, 10, 29)).entry_fee == 190_000
    assert schedule.at(at(MON, 10, 30)).entry_fee == 250_000
    assert schedule.at(datetime(1990, 1, 1, tzinfo=UTC)).entry_fee == 190_000  # before first: earliest
    with pytest.raises(TariffError):
        TariffSchedule(())


# ---------------------------------------------------------------- rounding


@pytest.mark.parametrize(
    ("step", "expected"),
    [(1, 231_167), (0, 231_167), (1_000, 232_000), (10_000, 240_000), (100_000, 300_000)],
)
def test_rounding_step_variants(step, expected):
    schedule = TariffSchedule.single(seed_values(rounding_step=step))
    assert price(at(MON, 10), at(MON, 11, 13), schedule=schedule).total == expected


# ---------------------------------------------------------------- values & calendar validation


@pytest.mark.parametrize(
    "bad",
    [
        {"entry_fee": -1},
        {"entry_fee": 1.5},
        {"hourly_rate": True},
        {"entry_minutes": 0},
        {"subscription_days": 0},
        {"motorcycle_night_fine": 1},
    ],
)
def test_tariff_values_validation(bad):
    with pytest.raises(TariffError):
        seed_values(**bad)


def test_tariff_values_roundtrip():
    values = seed_values(entry_fee=210_000)
    assert TariffValues.from_dict({**values.to_dict(), "unknown": 1}) == values


def test_calendar_serialization_and_validation():
    calendar = ParkingCalendar.from_parts({"0": ["10:00", "18:00"], "4": None}, [4], [MON])
    assert calendar.hours_for(MON) == DayHours(time(10), time(18))
    assert calendar.hours_for(FRI) is None
    assert calendar.is_free_day(MON)
    assert not calendar.is_free_day(THU)
    again = ParkingCalendar.from_parts(calendar.to_dict()["hours"], calendar.to_dict()["free_weekdays"])
    assert again.hours == calendar.hours
    with pytest.raises(TariffError):
        DayHours(time(20), time(9))
    closed = ParkingCalendar.from_parts(None, None)
    assert closed.free_weekdays == frozenset()
    assert closed.hours_for(MON) is None


def test_helper_functions():
    assert chargeable_minutes(at(MON, 9), at(MON, 21), CAL) == 660
    assert nights_crossed(at(MON, 9), at(TUE, 21), CAL) == 2
    assert is_after_hours(at(MON, 9, 29), CAL)
    assert not is_after_hours(at(MON, 9, 30), CAL)
    assert is_after_hours(at(MON, 20, 30), CAL)


def test_breakdown_lines():
    # 673 chargeable minutes → 2,131,167 → rounded to 2,140,000 (rounding line 8,833)
    result = price(at(MON, 10), at(TUE, 10, 13), coupon=True)
    assert result.rounding == 8_833
    keys = [line.key for line in result.lines()]
    assert keys == ["entry_fee", "extra_minutes", "rounding", "coupon", "night_fines"]
    assert sum(line.amount for line in result.lines()) == result.total


# ---------------------------------------------------------------- properties

_WINDOW = st.integers(min_value=0, max_value=14 * 24 * 60)
_STAY = st.integers(min_value=0, max_value=5 * 24 * 60)
_VEHICLE = st.sampled_from(list(VehicleType))


@settings(max_examples=300, deadline=None)
@given(start=_WINDOW, first=_STAY, extra=_STAY, vehicle=_VEHICLE)
def test_price_never_decreases_with_longer_stay(start, first, extra, vehicle):
    entry = at(MON, 0) + timedelta(minutes=start)
    shorter = price(entry, entry + timedelta(minutes=first), vehicle=vehicle)
    longer = price(entry, entry + timedelta(minutes=first + extra), vehicle=vehicle)
    assert longer.total >= shorter.total


@settings(max_examples=300, deadline=None)
@given(start=_WINDOW, stay=_STAY, seconds=st.integers(0, 59), vehicle=_VEHICLE, coupon=st.booleans())
def test_money_is_integer_and_rounded(start, stay, seconds, vehicle, coupon):
    entry = at(MON, 0) + timedelta(minutes=start)
    result = price(entry, entry + timedelta(minutes=stay, seconds=seconds), vehicle=vehicle, coupon=coupon)
    for f in fields(result):
        value = getattr(result, f.name)
        if f.name in ("tariff_effective_from", "flags"):
            continue
        assert type(value) is int, f.name
        assert value >= 0, f.name
    assert result.transient_fee % 10_000 == 0
    assert result.total == result.transient_fee + result.night_fines
    assert result.chargeable_minutes <= result.total_minutes


def test_long_stay_is_fast():
    import time as _time

    started = _time.perf_counter()
    for _ in range(200):
        price(at(MON, 10), at(MON, 10) + timedelta(days=30))
    elapsed_ms = (_time.perf_counter() - started) * 1000 / 200
    assert elapsed_ms < 100  # SPEC §2.4: exit price calculation < 100 ms


def test_calendar_without_any_opening_hours():
    calendar = ParkingCalendar(hours=dict.fromkeys(range(7)), free_weekdays=frozenset())
    result = price(at(MON, 10), at(WED, 10), calendar=calendar)
    assert result.total == 0
    assert result.nights == 0
    assert FLAG_AFTER_HOURS_ARRIVAL in result.flags
    assert FLAG_INSIDE_AT_OPENING not in result.flags
