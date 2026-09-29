"""Injectable clock. Domain code never calls ``datetime.now()`` directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock: returns a fixed instant that can be moved explicitly."""

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)

    def now_utc(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = ensure_utc(value)

    def advance(self, **kwargs: float) -> datetime:
        self._now += timedelta(**kwargs)
        return self._now


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime; naive values are rejected."""
    if value.tzinfo is None:
        raise ValueError("naive datetime is not allowed; pass an aware datetime")
    return value.astimezone(UTC)


SYSTEM_CLOCK = SystemClock()
