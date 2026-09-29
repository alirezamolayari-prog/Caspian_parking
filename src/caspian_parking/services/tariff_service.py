"""Tariff settings persistence and quoting (glue between the DB and the pure engine)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.tariff import (
    DayHours,
    ParkingCalendar,
    PriceBasis,
    PriceBreakdown,
    TariffError,
    TariffSchedule,
    TariffValues,
    TariffVersion,
    VehicleType,
    VisitKind,
    compute_price,
)
from caspian_parking.data.models import Holiday, TariffVersionRecord
from caspian_parking.data.repositories.system import SettingsRepository
from caspian_parking.data.repositories.tariff import (
    KEY_FREE_WEEKDAYS,
    KEY_HOURS,
    KEY_PRICE_BASIS,
    SEED_EFFECTIVE_FROM,
    HolidayRepository,
    TariffVersionRepository,
    seed_values,
)

# ---------------------------------------------------------------- loading


def load_schedule(session: Session) -> TariffSchedule:
    rows = [r for r in TariffVersionRepository(session).history() if r.is_active]
    if not rows:
        return TariffSchedule.single(seed_values(), SEED_EFFECTIVE_FROM)
    return TariffSchedule(tuple(TariffVersion(r.effective_from_utc, TariffValues.from_dict(r.amounts)) for r in rows))


def load_calendar(session: Session) -> ParkingCalendar:
    settings = SettingsRepository(session)
    holidays = [h.day for h in HolidayRepository(session).upcoming()]
    return ParkingCalendar.from_parts(settings.get(KEY_HOURS), settings.get(KEY_FREE_WEEKDAYS), holidays)


def load_price_basis(session: Session) -> PriceBasis:
    return PriceBasis(SettingsRepository(session).get(KEY_PRICE_BASIS, PriceBasis.ENTRY.value))


@dataclass(frozen=True)
class TariffContext:
    """Everything the engine needs, loaded once (cheap to reuse for many quotes)."""

    schedule: TariffSchedule
    calendar: ParkingCalendar
    basis: PriceBasis

    def quote(
        self,
        entry: datetime,
        exit_: datetime,
        vehicle: VehicleType = VehicleType.SEDAN,
        kind: VisitKind = VisitKind.TRANSIENT,
        coupon: bool = False,
        night_exempt: bool = False,
    ) -> PriceBreakdown:
        return compute_price(
            entry,
            exit_,
            self.calendar,
            self.schedule,
            vehicle,
            kind,
            self.basis,
            coupon=coupon,
            night_exempt=night_exempt,
        )


def load_context(session: Session) -> TariffContext:
    return TariffContext(load_schedule(session), load_calendar(session), load_price_basis(session))


# ---------------------------------------------------------------- editing


def add_tariff_version(
    session: Session, values: TariffValues, effective_from: datetime, now: datetime, note: str | None = None
) -> TariffVersionRecord:
    """New tariff version. Retroactive versions are refused (DECISIONS D-028)."""
    effective_from = ensure_utc(effective_from)
    if effective_from < ensure_utc(now).replace(second=0, microsecond=0):
        raise TariffError("tariff.retroactive")
    return TariffVersionRepository(session).add(
        TariffVersionRecord(effective_from_utc=effective_from, amounts=values.to_dict(), note=note)
    )


def cancel_tariff_version(session: Session, record: TariffVersionRecord, now: datetime, reason: str) -> None:
    """Only versions that have not started yet can be withdrawn (history is never rewritten)."""
    if record.effective_from_utc <= ensure_utc(now):
        raise TariffError("tariff.already_effective")
    TariffVersionRepository(session).deactivate(record, reason)


def save_calendar(session: Session, hours: dict[int, DayHours | None], free_weekdays: set[int]) -> None:
    calendar = ParkingCalendar(hours=hours, free_weekdays=frozenset(free_weekdays))
    settings = SettingsRepository(session)
    data = calendar.to_dict()
    settings.set(KEY_HOURS, data["hours"])
    settings.set(KEY_FREE_WEEKDAYS, data["free_weekdays"])


def save_price_basis(session: Session, basis: PriceBasis) -> None:
    SettingsRepository(session).set(KEY_PRICE_BASIS, basis.value)


def add_holiday(session: Session, day: date, title: str) -> Holiday:
    repo = HolidayRepository(session)
    existing = repo.by_day(day)
    if existing is not None:
        if existing.is_active and existing.title == title:
            return existing
        return repo.update(existing, title=title, is_active=True)
    return repo.add(Holiday(day=day, title=title))


def remove_holiday(session: Session, holiday: Holiday, reason: str) -> None:
    HolidayRepository(session).deactivate(holiday, reason)
