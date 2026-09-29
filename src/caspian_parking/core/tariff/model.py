"""Tariff values, versions and the parking calendar (pure data, integer Rial only)."""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.money import require_int


class VehicleType(StrEnum):
    SEDAN = "sedan"  # سواری
    VAN = "van"  # وانت
    TRUCK = "truck"
    MOTORCYCLE = "motorcycle"
    OTHER = "other"


class VisitKind(StrEnum):
    TRANSIENT = "transient"
    PASS_THROUGH = "pass_through"


class PassThroughType(StrEnum):
    TAXI = "taxi"
    COURIER = "courier"
    VAN_UNLOADING = "van_unloading"
    OTHER = "other"


class PriceBasis(StrEnum):
    ENTRY = "entry"  # tariff in force when the vehicle entered (default)
    EXIT = "exit"


class TariffError(ValueError):
    pass


@dataclass(frozen=True)
class TariffValues:
    """All amounts are integer Rial; all durations are whole minutes.

    Values come from settings / seed data (``resources/seed/site.json``), never from code.
    """

    entry_fee: int
    entry_minutes: int
    hourly_rate: int
    rounding_step: int
    motorcycle_flat: int
    night_fine: int
    motorcycle_night_fine: bool
    night_fine_grace_minutes: int
    pass_through_free_minutes: int
    subscription_price: int
    subscription_days: int

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "motorcycle_night_fine":
                if not isinstance(value, bool):
                    raise TariffError(f"{f.name} must be bool")
                continue
            try:
                require_int(value)
            except TypeError as exc:
                raise TariffError(f"{f.name} must be an integer") from exc
            if value < 0:
                raise TariffError(f"{f.name} must not be negative")
        if self.entry_minutes <= 0:
            raise TariffError("entry_minutes must be positive")
        if self.subscription_days <= 0:
            raise TariffError("subscription_days must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TariffValues:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class TariffVersion:
    effective_from: datetime
    values: TariffValues

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_from", ensure_utc(self.effective_from))


@dataclass(frozen=True)
class TariffSchedule:
    """Versioned tariffs; the version in force at an instant is the latest one that started before it."""

    versions: tuple[TariffVersion, ...]

    def __post_init__(self) -> None:
        if not self.versions:
            raise TariffError("at least one tariff version is required")
        ordered = tuple(sorted(self.versions, key=lambda v: v.effective_from))
        object.__setattr__(self, "versions", ordered)

    @classmethod
    def single(cls, values: TariffValues, effective_from: datetime | None = None) -> TariffSchedule:
        start = effective_from or datetime(2000, 1, 1, tzinfo=UTC)
        return cls((TariffVersion(start, values),))

    def version_at(self, moment: datetime) -> TariffVersion:
        moment = ensure_utc(moment)
        starts = [v.effective_from for v in self.versions]
        index = bisect.bisect_right(starts, moment) - 1
        return self.versions[max(0, index)]

    def at(self, moment: datetime) -> TariffValues:
        return self.version_at(moment).values


@dataclass(frozen=True)
class DayHours:
    """Opening hours of one local (Tehran) day; ``close`` is on the same day."""

    open: time
    close: time

    def __post_init__(self) -> None:
        if self.close <= self.open:
            raise TariffError("closing time must be after opening time")


@dataclass(frozen=True)
class ParkingCalendar:
    """Opening hours per weekday (Python weekday: Monday = 0), free weekdays and holidays."""

    hours: dict[int, DayHours | None] = field(default_factory=dict)  # missing weekday = closed
    free_weekdays: frozenset[int] = frozenset()
    holidays: frozenset[date] = frozenset()

    def hours_for(self, day: date) -> DayHours | None:
        return self.hours.get(day.weekday())

    def is_free_day(self, day: date) -> bool:
        return day.weekday() in self.free_weekdays or day in self.holidays

    def opening_utc(self, day: date) -> tuple[datetime, datetime] | None:
        hours = self.hours_for(day)
        if hours is None:
            return None
        return (
            local_to_utc(datetime.combine(day, hours.open)),
            local_to_utc(datetime.combine(day, hours.close)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours": {
                str(k): (None if v is None else [v.open.strftime("%H:%M"), v.close.strftime("%H:%M")])
                for k, v in sorted(self.hours.items())
            },
            "free_weekdays": sorted(self.free_weekdays),
        }

    @classmethod
    def from_parts(
        cls, hours: dict[str, Any] | None, free_weekdays: list[int] | None, holidays: list[date] | None = None
    ) -> ParkingCalendar:
        parsed: dict[int, DayHours | None] = dict.fromkeys(range(7))
        for key, value in (hours or {}).items():
            if value is None:
                parsed[int(key)] = None
            else:
                opening, closing = (time.fromisoformat(v) for v in value)
                parsed[int(key)] = DayHours(opening, closing)
        free = frozenset(int(x) for x in free_weekdays or ())
        return cls(parsed, free, frozenset(holidays or ()))
