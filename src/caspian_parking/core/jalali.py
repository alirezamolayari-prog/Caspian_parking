"""Jalali (Shamsi) calendar and Tehran local time helpers.

Storage is UTC; everything shown to users is Tehran local time in the Jalali calendar.
Functions return Latin digits; the UI layer converts to Persian digits for display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import jdatetime

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.digits import normalize_input

TEHRAN = ZoneInfo("Asia/Tehran")

_JDATE_RE = re.compile(r"^\s*(\d{2,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*$")


@dataclass(frozen=True, order=True)
class JalaliDate:
    year: int
    month: int
    day: int

    def to_gregorian(self) -> date:
        return jdatetime.date(self.year, self.month, self.day).togregorian()

    @classmethod
    def from_gregorian(cls, value: date) -> JalaliDate:
        j = jdatetime.date.fromgregorian(date=value)
        return cls(j.year, j.month, j.day)

    def isoformat(self, sep: str = "/") -> str:
        return f"{self.year:04d}{sep}{self.month:02d}{sep}{self.day:02d}"

    def __str__(self) -> str:
        return self.isoformat()


def to_local(value: datetime) -> datetime:
    """UTC (aware) → Tehran local aware datetime."""
    return ensure_utc(value).astimezone(TEHRAN)


def local_to_utc(value: datetime) -> datetime:
    """Tehran local time (naive = Tehran wall clock, or aware) → aware UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=TEHRAN)
    return value.astimezone(UTC)


def local_date(value: datetime) -> date:
    """Tehran calendar date (Gregorian) of a UTC instant."""
    return to_local(value).date()


def jalali_of(value: datetime | date) -> JalaliDate:
    """Jalali date of a UTC instant (Tehran day) or of a Gregorian calendar date."""
    if isinstance(value, datetime):
        return JalaliDate.from_gregorian(local_date(value))
    return JalaliDate.from_gregorian(value)


def format_jdate(value: datetime | date, sep: str = "/") -> str:
    return jalali_of(value).isoformat(sep)


def format_time(value: datetime, seconds: bool = False) -> str:
    local = to_local(value)
    return local.strftime("%H:%M:%S" if seconds else "%H:%M")


def format_jdatetime(value: datetime, seconds: bool = False) -> str:
    return f"{format_jdate(value)} {format_time(value, seconds)}"


def parse_jdate(text: str) -> JalaliDate:
    """Parse ``1405/07/06`` (any digits, separators / - .). Two-digit years mean 14xx."""
    match = _JDATE_RE.match(normalize_input(text))
    if not match:
        raise ValueError(f"invalid Jalali date: {text!r}")
    year, month, day = (int(g) for g in match.groups())
    if year < 100:
        year += 1400
    try:
        jdatetime.date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"invalid Jalali date: {text!r}") from exc
    return JalaliDate(year, month, day)


def start_of_local_day_utc(day: date) -> datetime:
    """UTC instant of 00:00 Tehran on the given Gregorian date."""
    return local_to_utc(datetime.combine(day, time(0, 0)))


def local_day_range_utc(day: date) -> tuple[datetime, datetime]:
    """[start, end) UTC of a Tehran calendar day."""
    return start_of_local_day_utc(day), start_of_local_day_utc(day + timedelta(days=1))


def jalali_month_length(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if jdatetime.date(year, 1, 1).isleap() else 29


def jalali_month_range_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """[start, end) UTC covering a whole Jalali month in Tehran time."""
    first = JalaliDate(year, month, 1).to_gregorian()
    last = JalaliDate(year, month, jalali_month_length(year, month)).to_gregorian()
    return start_of_local_day_utc(first), start_of_local_day_utc(last + timedelta(days=1))


def jalali_weekday(value: date) -> int:
    """Persian week index: Saturday = 0 … Friday = 6."""
    return (value.weekday() + 2) % 7
