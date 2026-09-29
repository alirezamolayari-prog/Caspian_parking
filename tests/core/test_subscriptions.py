"""Subscription date logic (SPEC §4.6), including negative subscriptions."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.subscriptions import (
    EntryStatus,
    Light,
    LightThresholds,
    concurrency_exceeded,
    days_left,
    entry_status,
    light_for,
    overdue_days,
    renew,
    sort_follow_up,
    used_days,
)

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
DAY = timedelta(days=1)


# ---------------------------------------------------------------- lights


@pytest.mark.parametrize(
    ("remaining", "light"),
    [
        (timedelta(days=30), Light.GREEN),
        (timedelta(days=5, seconds=1), Light.GREEN),
        (timedelta(days=5), Light.AMBER),
        (timedelta(days=2, seconds=1), Light.AMBER),
        (timedelta(hours=48), Light.RED),
        (timedelta(seconds=1), Light.RED),
        (timedelta(0), Light.BLACK),
        (-timedelta(days=3), Light.BLACK),
    ],
)
def test_light_boundaries(remaining, light):
    assert light_for(NOW + remaining, NOW) is light


def test_light_thresholds_are_configurable():
    thresholds = LightThresholds(amber_days=10, red_hours=72)
    assert light_for(NOW + timedelta(days=9), NOW, thresholds) is Light.AMBER
    assert light_for(NOW + timedelta(hours=70), NOW, thresholds) is Light.RED
    assert light_for(None, NOW) is Light.BLACK


@pytest.mark.parametrize(
    ("delta", "days"),
    [
        (timedelta(days=30), 30),
        (timedelta(days=29, hours=1), 30),
        (timedelta(hours=1), 1),
        (timedelta(0), 0),
        (-timedelta(hours=1), -1),
        (-timedelta(days=1), -1),
        (-timedelta(days=1, seconds=1), -2),
        (-timedelta(days=7), -7),
    ],
)
def test_days_left_including_negative(delta, days):
    assert days_left(NOW + delta, NOW) == days
    assert overdue_days(NOW + delta, NOW) == max(0, -days)


def test_days_left_without_subscription():
    assert days_left(None, NOW) == 0


# ---------------------------------------------------------------- renewals


def test_first_payment_starts_now():
    renewal = renew(None, NOW, 30)
    assert renewal.new_end == NOW + 30 * DAY
    assert not renewal.early
    assert renewal.deducted_days == 0


def test_early_payment_extends_from_current_end():
    end = NOW + 3 * DAY
    renewal = renew(end, NOW, 30)
    assert renewal.early
    assert renewal.new_end == end + 30 * DAY


def test_late_payment_without_entries_starts_at_payment():
    renewal = renew(NOW - 4 * DAY, NOW, 30)
    assert renewal.new_end == NOW + 30 * DAY
    assert renewal.used_days == ()


def test_negative_subscription_deducts_distinct_days_entered():
    end = local_to_utc(datetime(2026, 9, 20, 18, 0))
    entries = [
        local_to_utc(datetime(2026, 9, 19, 10)),  # before expiry → not counted
        local_to_utc(datetime(2026, 9, 21, 10)),
        local_to_utc(datetime(2026, 9, 21, 16)),  # same day twice → one day
        local_to_utc(datetime(2026, 9, 23, 9)),
        local_to_utc(datetime(2026, 9, 25, 11)),
        local_to_utc(datetime(2026, 9, 29, 11)),  # after payment → not counted
    ]
    paid_at = local_to_utc(datetime(2026, 9, 28, 12))
    renewal = renew(end, paid_at, 30, entries)
    assert renewal.used_days == (date(2026, 9, 21), date(2026, 9, 23), date(2026, 9, 25))
    assert renewal.new_end == paid_at + 27 * DAY


def test_deduction_never_exceeds_period():
    end = NOW - 60 * DAY
    entries = [NOW - i * DAY for i in range(1, 50)]
    renewal = renew(end, NOW, 30, entries)
    assert renewal.new_end == NOW
    assert renewal.deducted_days == 49


def test_renew_rejects_bad_period():
    with pytest.raises(ValueError, match="period"):
        renew(None, NOW, 0)


def test_used_days_uses_tehran_dates():
    # 22:00 UTC = 01:30 next day in Tehran
    entry = datetime(2026, 9, 27, 22, 0, tzinfo=UTC)
    assert used_days([entry], NOW - 5 * DAY, NOW) == [date(2026, 9, 28)]


# ---------------------------------------------------------------- entry status


@pytest.mark.parametrize(
    ("end", "allowed", "max_days", "status"),
    [
        (NOW + DAY, False, 10, EntryStatus.ACTIVE),
        (NOW - DAY, False, 10, EntryStatus.EXPIRED),
        (NOW - DAY, True, 10, EntryStatus.NEGATIVE),
        (NOW - 10 * DAY, True, 10, EntryStatus.NEGATIVE),
        (NOW - 10 * DAY - timedelta(seconds=1), True, 10, EntryStatus.EXPIRED),
        (NOW - 3 * DAY, True, 2, EntryStatus.EXPIRED),
        (None, True, 10, EntryStatus.NONE),
    ],
)
def test_entry_status(end, allowed, max_days, status):
    assert entry_status(end, NOW, allowed, max_days) is status


def test_concurrency_rule():
    assert not concurrency_exceeded(0, 1)
    assert concurrency_exceeded(1, 1)
    assert not concurrency_exceeded(1, 2)
    assert concurrency_exceeded(1, 0)  # treated as 1


def test_follow_up_order():
    rows = [
        (Light.GREEN, 20, "sara"),
        (Light.AMBER, 4, "ali"),
        (Light.BLACK, -2, "reza"),
        (Light.RED, 1, "mina"),
        (Light.BLACK, -9, "hadi"),
        (Light.AMBER, 2, "nika"),
    ]
    assert [r[2] for r in sort_follow_up(rows)] == ["hadi", "reza", "mina", "nika", "ali"]


@given(
    current=st.integers(min_value=-100, max_value=100),
    entries=st.lists(st.integers(min_value=-100, max_value=0), max_size=40),
)
def test_renewal_properties(current, entries):
    end = NOW + current * DAY
    renewal = renew(end, NOW, 30, [NOW + e * DAY for e in entries])
    assert renewal.new_end >= NOW
    assert renewal.new_end <= max(end, NOW) + 30 * DAY
    if current > 0:
        assert renewal.new_end == end + 30 * DAY
