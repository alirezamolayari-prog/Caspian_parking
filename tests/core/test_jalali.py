from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from caspian_parking.core import jalali
from caspian_parking.core.jalali import JalaliDate


def test_known_conversions():
    # 1 Farvardin 1405 = 21 March 2026 (Nowruz)
    assert JalaliDate(1405, 1, 1).to_gregorian() == date(2026, 3, 21)
    assert JalaliDate.from_gregorian(date(2026, 9, 28)) == JalaliDate(1405, 7, 6)
    assert JalaliDate.from_gregorian(date(2025, 3, 20)) == JalaliDate(1403, 12, 30)


def test_tehran_is_utc_plus_0330_without_dst():
    summer = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    winter = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert jalali.to_local(summer).strftime("%H:%M") == "11:30"
    assert jalali.to_local(winter).strftime("%H:%M") == "11:30"


def test_format_uses_tehran_day():
    # 21:00 UTC is 00:30 next day in Tehran
    value = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)
    assert jalali.format_jdate(value) == "1405/07/06"
    assert jalali.format_time(value) == "00:30"
    assert jalali.format_jdatetime(value, seconds=True) == "1405/07/06 00:30:00"


def test_local_to_utc_roundtrip():
    naive_local = datetime(2026, 9, 28, 14, 32)
    utc = jalali.local_to_utc(naive_local)
    assert utc == datetime(2026, 9, 28, 11, 2, tzinfo=UTC)
    assert jalali.to_local(utc).replace(tzinfo=None) == naive_local


def test_naive_utc_rejected():
    with pytest.raises(ValueError, match="naive"):
        jalali.to_local(datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1405/07/06", JalaliDate(1405, 7, 6)),
        ("۱۴۰۵/۷/۶", JalaliDate(1405, 7, 6)),
        ("1405-7-6", JalaliDate(1405, 7, 6)),
        ("05.07.06", JalaliDate(1405, 7, 6)),
    ],
)
def test_parse_jdate(text, expected):
    assert jalali.parse_jdate(text) == expected


@pytest.mark.parametrize("text", ["1405/13/01", "1405/07/32", "hello", "1403/12/31"])
def test_parse_jdate_invalid(text):
    with pytest.raises(ValueError, match="invalid Jalali"):
        jalali.parse_jdate(text)


def test_month_lengths_and_range():
    assert jalali.jalali_month_length(1405, 1) == 31
    assert jalali.jalali_month_length(1405, 7) == 30
    assert jalali.jalali_month_length(1403, 12) == 30  # leap year
    assert jalali.jalali_month_length(1404, 12) == 29
    start, end = jalali.jalali_month_range_utc(1405, 7)
    assert jalali.format_jdatetime(start) == "1405/07/01 00:00"
    assert jalali.format_jdatetime(end) == "1405/08/01 00:00"


def test_day_range_and_weekday():
    start, end = jalali.local_day_range_utc(date(2026, 9, 28))
    assert (end - start).total_seconds() == 86400
    assert jalali.jalali_weekday(date(2026, 9, 26)) == 0  # Saturday
    assert jalali.jalali_weekday(date(2026, 10, 2)) == 6  # Friday
