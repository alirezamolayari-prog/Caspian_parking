"""Subscription date logic (SPEC §4.6) — pure and exhaustively tested.

* a period is ``days`` × 24 h from the payment moment
* early payment extends from the current end (new end = current end + days)
* status light: green > amber_days left, amber ≤ amber_days, red ≤ red_hours, black = expired
* negative subscription (اشتراک منفی): an expired subscriber may keep entering for up to ``max_days``
  when a permitted user allowed it; on payment the *distinct days actually entered* during the
  overdue period are deducted: new end = payment + days − used days
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.jalali import local_date

SECONDS_PER_DAY = 86_400
HOURS_PER_DAY = 24


class Light(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    BLACK = "black"


LIGHT_URGENCY = {Light.BLACK: 0, Light.RED: 1, Light.AMBER: 2, Light.GREEN: 3}


@dataclass(frozen=True)
class LightThresholds:
    amber_days: int = 5
    red_hours: int = 48


class EntryStatus(StrEnum):
    ACTIVE = "active"  # valid subscription
    NEGATIVE = "negative"  # expired, but allowed to enter (⚫ مجاز)
    EXPIRED = "expired"  # expired → handled as a transient
    NONE = "none"  # never paid


def light_for(end: datetime | None, now: datetime, thresholds: LightThresholds = LightThresholds()) -> Light:
    if end is None:
        return Light.BLACK
    remaining = ensure_utc(end) - ensure_utc(now)
    if remaining <= timedelta(0):
        return Light.BLACK
    if remaining <= timedelta(hours=thresholds.red_hours):
        return Light.RED
    if remaining <= timedelta(days=thresholds.amber_days):
        return Light.AMBER
    return Light.GREEN


def days_left(end: datetime | None, now: datetime) -> int:
    """Whole days until the end (rounded up while active); negative = days since expiry (rounded up)."""
    if end is None:
        return 0
    seconds = (ensure_utc(end) - ensure_utc(now)).total_seconds()
    if seconds >= 0:
        return -(-int(seconds) // SECONDS_PER_DAY)
    return -(-int(-seconds) // SECONDS_PER_DAY) * -1


def overdue_days(end: datetime | None, now: datetime) -> int:
    """Days since expiry (0 while active)."""
    remaining = days_left(end, now)
    return -remaining if remaining < 0 else 0


def entry_status(
    end: datetime | None, now: datetime, negative_allowed: bool = False, negative_max_days: int = 10
) -> EntryStatus:
    if end is None:
        return EntryStatus.NONE
    if ensure_utc(end) > ensure_utc(now):
        return EntryStatus.ACTIVE
    overdue = ensure_utc(now) - ensure_utc(end)
    if negative_allowed and overdue <= timedelta(days=negative_max_days):
        return EntryStatus.NEGATIVE
    return EntryStatus.EXPIRED


def used_days(entries: Iterable[datetime], since: datetime, until: datetime) -> list[date]:
    """Distinct Tehran dates with at least one entry in (since, until] — sorted."""
    since, until = ensure_utc(since), ensure_utc(until)
    days = {local_date(e) for e in entries if since < ensure_utc(e) <= until}
    return sorted(days)


@dataclass(frozen=True)
class Renewal:
    previous_end: datetime | None
    new_end: datetime
    used_days: tuple[date, ...] = ()
    early: bool = False

    @property
    def deducted_days(self) -> int:
        return len(self.used_days)


def renew(
    current_end: datetime | None,
    paid_at: datetime,
    days: int,
    entries_while_overdue: Iterable[datetime] = (),
) -> Renewal:
    """New end after a payment of one period of ``days`` days."""
    if days <= 0:
        raise ValueError("period must be positive")
    paid_at = ensure_utc(paid_at)
    if current_end is not None and ensure_utc(current_end) > paid_at:
        return Renewal(current_end, ensure_utc(current_end) + timedelta(days=days), early=True)
    used: list[date] = []
    if current_end is not None:
        used = used_days(entries_while_overdue, current_end, paid_at)
    deduct = min(len(used), days)
    return Renewal(current_end, paid_at + timedelta(days=days - deduct), tuple(used))


def concurrency_exceeded(inside_for_subscription: int, max_concurrent: int) -> bool:
    """A second plate of the same subscription while another is inside → alert, treat as transient."""
    return inside_for_subscription >= max(1, max_concurrent)


def sort_follow_up(rows: Iterable[tuple[Light, int, str]]) -> list[tuple[Light, int, str]]:
    """Follow-up list order: black (most overdue first), red, amber (fewest days first); green excluded."""
    relevant = [row for row in rows if row[0] is not Light.GREEN]
    return sorted(relevant, key=lambda row: (LIGHT_URGENCY[row[0]], row[1], row[2]))
