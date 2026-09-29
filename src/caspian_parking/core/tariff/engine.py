"""Tariff engine (SPEC §4.5). Pure, deterministic, integer Rial only.

Rules implemented here:
* duration in whole minutes = floor(seconds / 60); a partial minute is never charged
* only minutes inside opening hours of *paid* days are chargeable
* car: entry fee covers the first ``entry_minutes`` chargeable minutes; after that
  ``extra = ceil(hourly_rate × extra_minutes / 60)``; the transient fee is rounded UP to ``rounding_step``
* motorcycle: flat fee when there is any chargeable minute (DECISIONS D-010)
* night fine per closing time crossed while inside (+ grace), also on free days (D-009)
* pass-through: free up to ``pass_through_free_minutes``; longer stays are priced as transient and flagged
* coupon: transient fee becomes zero; night fines always remain
* after-hours arrivals are security records only; flagged when still inside at the next opening
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.jalali import local_date
from caspian_parking.core.money import ceil_div, round_up_to_step
from caspian_parking.core.tariff.model import (
    ParkingCalendar,
    PriceBasis,
    TariffError,
    TariffSchedule,
    TariffValues,
    VehicleType,
    VisitKind,
)

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60

# flags
FLAG_PASS_THROUGH_FREE = "pass_through_free"
FLAG_PASS_THROUGH_OVER_LIMIT = "pass_through_over_limit"
FLAG_AFTER_HOURS_ARRIVAL = "after_hours_arrival"
FLAG_INSIDE_AT_OPENING = "inside_at_opening"
FLAG_COUPON = "coupon"
FLAG_NIGHT_EXEMPT = "night_exempt"
FLAG_NO_CHARGEABLE_TIME = "no_chargeable_time"
FLAG_OVERNIGHT = "overnight"


@dataclass(frozen=True)
class PriceLine:
    key: str
    amount: int
    quantity: int = 1


@dataclass(frozen=True)
class PriceBreakdown:
    total_minutes: int
    chargeable_minutes: int
    entry_fee: int
    extra_minutes: int
    extra_amount: int
    rounding: int
    transient_fee: int
    coupon_discount: int
    nights: int
    night_fine_each: int
    night_fines: int
    total: int
    tariff_effective_from: datetime
    flags: frozenset[str] = field(default_factory=frozenset)

    @property
    def fee_before_coupon(self) -> int:
        return self.transient_fee + self.coupon_discount

    def lines(self) -> list[PriceLine]:
        """Receipt / report lines (only non-zero parts)."""
        items = [
            PriceLine("entry_fee", self.entry_fee),
            PriceLine("extra_minutes", self.extra_amount, self.extra_minutes),
            PriceLine("rounding", self.rounding),
            PriceLine("coupon", -self.coupon_discount),
            PriceLine("night_fines", self.night_fines, self.nights),
        ]
        return [line for line in items if line.amount]


def _overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0, int((end - start).total_seconds()))


def _days(entry: datetime, exit_: datetime) -> list:
    first, last = local_date(entry), local_date(exit_)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def chargeable_minutes(entry: datetime, exit_: datetime, calendar: ParkingCalendar) -> int:
    """Whole minutes inside opening hours on paid days."""
    seconds = 0
    for day in _days(entry, exit_):
        if calendar.is_free_day(day):
            continue
        window = calendar.opening_utc(day)
        if window is not None:
            seconds += _overlap_seconds(entry, exit_, *window)
    return seconds // SECONDS_PER_MINUTE


def nights_crossed(entry: datetime, exit_: datetime, calendar: ParkingCalendar, grace_minutes: int = 0) -> int:
    """Closing times (on any day, free or paid) the vehicle was inside for, plus grace."""
    grace = timedelta(minutes=grace_minutes)
    count = 0
    for day in _days(entry, exit_):
        window = calendar.opening_utc(day)
        if window is None:
            continue
        closing = window[1]
        if entry < closing and exit_ > closing + grace:
            count += 1
    return count


def is_after_hours(moment: datetime, calendar: ParkingCalendar) -> bool:
    window = calendar.opening_utc(local_date(moment))
    return window is None or not (window[0] <= moment < window[1])


def _next_opening(moment: datetime, calendar: ParkingCalendar, horizon_days: int = 14) -> datetime | None:
    day = local_date(moment)
    for offset in range(horizon_days + 1):
        window = calendar.opening_utc(day + timedelta(days=offset))
        if window is not None and window[0] > moment:
            return window[0]
    return None


def car_fee(minutes: int, values: TariffValues) -> tuple[int, int, int, int, int]:
    """(entry_fee, extra_minutes, extra_amount, rounding, fee) for ``minutes`` chargeable minutes."""
    if minutes <= 0:
        return 0, 0, 0, 0, 0
    extra_minutes = max(0, minutes - values.entry_minutes)
    extra_amount = ceil_div(values.hourly_rate * extra_minutes, MINUTES_PER_HOUR)
    subtotal = values.entry_fee + extra_amount
    fee = round_up_to_step(subtotal, values.rounding_step)
    return values.entry_fee, extra_minutes, extra_amount, fee - subtotal, fee


def compute_price(
    entry: datetime,
    exit_: datetime,
    calendar: ParkingCalendar,
    schedule: TariffSchedule,
    vehicle: VehicleType = VehicleType.SEDAN,
    kind: VisitKind = VisitKind.TRANSIENT,
    basis: PriceBasis = PriceBasis.ENTRY,
    coupon: bool = False,
    night_exempt: bool = False,
) -> PriceBreakdown:
    entry = ensure_utc(entry)
    exit_ = ensure_utc(exit_)
    if exit_ < entry:
        raise TariffError("exit time is before entry time")
    version = schedule.version_at(entry if basis is PriceBasis.ENTRY else exit_)
    values = version.values
    flags: set[str] = set()

    total_minutes = int((exit_ - entry).total_seconds()) // SECONDS_PER_MINUTE
    minutes = chargeable_minutes(entry, exit_, calendar)

    after_hours = is_after_hours(entry, calendar)
    if after_hours:
        flags.add(FLAG_AFTER_HOURS_ARRIVAL)
        opening = _next_opening(entry, calendar)
        if opening is not None and exit_ > opening:
            flags.add(FLAG_INSIDE_AT_OPENING)

    free_pass = kind is VisitKind.PASS_THROUGH and total_minutes <= values.pass_through_free_minutes
    if kind is VisitKind.PASS_THROUGH:
        flags.add(FLAG_PASS_THROUGH_FREE if free_pass else FLAG_PASS_THROUGH_OVER_LIMIT)

    entry_fee = extra_minutes = extra_amount = rounding = fee = 0
    if not free_pass:
        if vehicle is VehicleType.MOTORCYCLE:
            if minutes > 0:
                entry_fee = values.motorcycle_flat
                fee = round_up_to_step(entry_fee, values.rounding_step)
                rounding = fee - entry_fee
        else:
            entry_fee, extra_minutes, extra_amount, rounding, fee = car_fee(minutes, values)
    if minutes == 0 and not free_pass:
        flags.add(FLAG_NO_CHARGEABLE_TIME)

    coupon_discount = 0
    if coupon and fee > 0:
        coupon_discount = fee
        fee = 0
        flags.add(FLAG_COUPON)

    nights = nights_crossed(entry, exit_, calendar, values.night_fine_grace_minutes)
    if nights:
        flags.add(FLAG_OVERNIGHT)
    fine_applies = not night_exempt and (vehicle is not VehicleType.MOTORCYCLE or values.motorcycle_night_fine)
    if nights and night_exempt:
        flags.add(FLAG_NIGHT_EXEMPT)
    night_each = values.night_fine if fine_applies else 0
    night_fines = nights * night_each

    return PriceBreakdown(
        total_minutes=total_minutes,
        chargeable_minutes=minutes,
        entry_fee=entry_fee,
        extra_minutes=extra_minutes,
        extra_amount=extra_amount,
        rounding=rounding,
        transient_fee=fee,
        coupon_discount=coupon_discount,
        nights=nights,
        night_fine_each=night_each,
        night_fines=night_fines,
        total=fee + night_fines,
        tariff_effective_from=version.effective_from,
        flags=frozenset(flags),
    )
