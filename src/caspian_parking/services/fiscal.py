"""Fiscal years (SPEC §4.13): define, close (archive counters, read-only), open the next year.

Carry-over is automatic: subscriptions, wallet balances, debts and vehicles inside are live data
that simply continue into the new year. Only the immutable counters restart at the new year.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import JalaliDate, jalali_month_length, jalali_of, start_of_local_day_utc
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import FiscalYear
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.services.context import AppContext


class FiscalError(RuntimeError):
    pass


class FiscalYearRepository(ReferenceRepository[FiscalYear]):
    model = FiscalYear


def jalali_year_bounds(year: int) -> tuple[date, date]:
    return JalaliDate(year, 1, 1).to_gregorian(), JalaliDate(year, 12, jalali_month_length(year, 12)).to_gregorian()


def ensure_fiscal_year(session: Session, now: datetime) -> FiscalYear:
    """Make sure an open fiscal year exists (first start: the current Jalali year)."""
    current = open_year(session)
    if current is not None:
        return current
    year = jalali_of(now).year
    start, end = jalali_year_bounds(year)
    return FiscalYearRepository(session).add(FiscalYear(name=str(year), start_date=start, end_date=end, status="open"))


def open_year(session: Session) -> FiscalYear | None:
    return session.scalar(select(FiscalYear).where(FiscalYear.status == "open").order_by(FiscalYear.start_date.desc()))


def years(session: Session) -> list[FiscalYear]:
    return list(session.scalars(select(FiscalYear).order_by(FiscalYear.start_date.desc())))


def year_start_utc(year: FiscalYear) -> datetime:
    return start_of_local_day_utc(year.start_date)


def year_end_utc(year: FiscalYear) -> datetime:
    return start_of_local_day_utc(year.end_date + timedelta(days=1))


def locked_before(session: Session) -> datetime | None:
    """Writes dated before this instant belong to a closed year and are refused."""
    current = open_year(session)
    closed = session.scalar(select(FiscalYear).where(FiscalYear.status == "closed").limit(1))
    if current is None or closed is None:
        return None
    return year_start_utc(current)


def close_year(ctx: AppContext, confirm_name: str, reason: str) -> FiscalYear:
    """Close the open year: archive its final counters, mark it closed and open the next year."""
    if not ctx.can(Permission.CLOSE_FISCAL_YEAR):
        raise FiscalError("gate.permission_denied")
    from caspian_parking.services.gate_service import GateService

    now = ctx.clock.now_utc()
    with ctx.read() as session:
        current = open_year(session)
        if current is None:
            raise FiscalError("fiscal.no_open_year")
        start, end = year_start_utc(current), year_end_utc(current)
    if confirm_name.strip() != current.name:
        raise FiscalError("fiscal.confirm_mismatch")
    if now < start + timedelta(days=1):
        raise FiscalError("fiscal.too_early")
    counters = GateService(ctx).counters(since=start, until=end)
    with ctx.uow(reason=reason) as session:
        repo = FiscalYearRepository(session)
        year = session.get(FiscalYear, current.id)
        assert year is not None
        repo.update(
            year,
            status="closed",
            closed_at_utc=now,
            closed_by=ctx.user.id if ctx.user else None,
            final_counters=counters,
        )
        next_start = year.end_date + timedelta(days=1)
        next_jalali = JalaliDate.from_gregorian(next_start)
        next_end = (
            jalali_year_bounds(next_jalali.year)[1]
            if next_jalali.month == 1 and next_jalali.day == 1
            else (next_start + (year.end_date - year.start_date))
        )
        new_year = repo.add(
            FiscalYear(name=str(next_jalali.year), start_date=next_start, end_date=next_end, status="open")
        )
    ctx.locked_before = year_start_utc(new_year)
    return new_year
