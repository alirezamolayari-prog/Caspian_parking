"""Repositories and seeding for tariff versions, holidays and calendar settings."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caspian_parking.config.defaults import site_seed
from caspian_parking.core.tariff import TariffValues
from caspian_parking.data.models import Holiday, TariffVersionRecord
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.data.repositories.system import SettingsRepository

KEY_HOURS = "calendar.hours"
KEY_FREE_WEEKDAYS = "calendar.free_weekdays"
KEY_PRICE_BASIS = "tariff.price_basis"
SEED_EFFECTIVE_FROM = datetime(2000, 1, 1, tzinfo=UTC)


class TariffVersionRepository(ReferenceRepository[TariffVersionRecord]):
    model = TariffVersionRecord

    def history(self) -> list[TariffVersionRecord]:
        stmt = select(TariffVersionRecord).order_by(TariffVersionRecord.effective_from_utc.desc())
        return list(self.session.scalars(stmt))


class HolidayRepository(ReferenceRepository[Holiday]):
    model = Holiday

    def by_day(self, day: date) -> Holiday | None:
        return self.session.scalar(select(Holiday).where(Holiday.day == day))

    def upcoming(self, since: date | None = None) -> list[Holiday]:
        stmt = select(Holiday).where(Holiday.is_active.is_(True)).order_by(Holiday.day)
        if since is not None:
            stmt = stmt.where(Holiday.day >= since)
        return list(self.session.scalars(stmt))


def seed_values() -> TariffValues:
    return TariffValues.from_dict(site_seed()["tariff"])


def seed_tariffs(session: Session) -> None:
    """First start: one tariff version from the seed file, calendar settings from the seed file."""
    versions = TariffVersionRepository(session)
    if versions.count() == 0:
        versions.add(
            TariffVersionRecord(effective_from_utc=SEED_EFFECTIVE_FROM, amounts=seed_values().to_dict(), note="seed")
        )
    settings = SettingsRepository(session)
    calendar = site_seed()["calendar"]
    if settings.by_key(KEY_HOURS) is None:
        settings.set(KEY_HOURS, calendar["hours"])
    if settings.by_key(KEY_FREE_WEEKDAYS) is None:
        settings.set(KEY_FREE_WEEKDAYS, calendar["free_weekdays"])
    if settings.by_key(KEY_PRICE_BASIS) is None:
        settings.set(KEY_PRICE_BASIS, calendar["price_basis"])
