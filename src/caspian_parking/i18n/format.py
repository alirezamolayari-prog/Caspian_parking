"""Display formatting for the UI: Persian digits, Jalali dates, bidi-safe runs."""

from __future__ import annotations

from datetime import date, datetime

from caspian_parking.core import jalali
from caspian_parking.core.digits import to_persian_digits
from caspian_parking.core.money import format_rial
from caspian_parking.i18n import tr
from caspian_parking.i18n.bidi import ltr


def fa_digits(value: object) -> str:
    return to_persian_digits(str(value))


def fa_number(value: int) -> str:
    """Integer with Persian digits and thousands separators, isolated LTR."""
    return ltr(format_rial(value))


def fa_money(value: int, with_unit: bool = True) -> str:
    text = ltr(format_rial(value))
    return f"{text} {tr('money.unit')}" if with_unit else text


def fa_date(value: datetime | date) -> str:
    return ltr(fa_digits(jalali.format_jdate(value)))


def fa_time(value: datetime, seconds: bool = False) -> str:
    return ltr(fa_digits(jalali.format_time(value, seconds)))


def fa_datetime(value: datetime, seconds: bool = False) -> str:
    return f"{fa_date(value)} {fa_time(value, seconds)}"


def fa_ltr(value: object) -> str:
    """Codes, ticket numbers, paths: Persian digits, isolated LTR."""
    return ltr(fa_digits(value))


def weekday_name(py_weekday: int) -> str:
    """Name of a Python weekday (Monday = 0)."""
    return tr(f"weekday.{py_weekday}")


def jalali_month_name(month: int) -> str:
    return tr(f"jmonth.{month}")


def fa_long_date(value: datetime) -> str:
    """``دوشنبه ۶ مهر ۱۴۰۵`` for a UTC instant (Tehran day)."""
    local = jalali.to_local(value)
    j = jalali.jalali_of(value)
    return f"{weekday_name(local.weekday())} {fa_digits(j.day)} {jalali_month_name(j.month)} {fa_digits(j.year)}"


def fa_duration(minutes: int) -> str:
    hours, mins = divmod(max(0, minutes), 60)
    if hours:
        return tr("duration.hours_minutes", h=fa_digits(hours), m=fa_digits(mins))
    return tr("duration.minutes", m=fa_digits(mins))
