"""Occupancy & peaks (12) and the monthly executive summary (13)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import jalali_month_range_utc, jalali_of, to_local
from caspian_parking.data.models import ActiveSession, Cancellation, Debt, EntryEvent, Level, Person, Visit
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, fa_duration, fa_money, jalali_month_name, weekday_name
from caspian_parking.services.exporters import Section
from caspian_parking.services.reports.base import ReportParams, ReportResult, previous_month
from caspian_parking.services.reports.money import financial

PERSIAN_WEEK = (5, 6, 0, 1, 2, 3, 4)  # Saturday … Friday


def occupancy_samples(
    session: Session, start: datetime, end: datetime, step: timedelta = timedelta(hours=1)
) -> list[tuple[datetime, int]]:
    """Number of vehicles inside at every ``step`` in [start, end) (sweep over entries and exits)."""
    intervals: list[tuple[datetime, datetime]] = []
    # "entered before the end and still inside at the start", split so that each part uses an index
    # (one combined condition makes SQLite read every older visit — years of history)
    live = Visit.status != "cancelled"
    parts = (
        (Visit.entry_at_utc >= start, Visit.entry_at_utc < end),  # entered during the period
        (Visit.exit_at_utc >= start, Visit.entry_at_utc < start),  # entered before, left after the start
        (Visit.exit_at_utc.is_(None), Visit.entry_at_utc < start),  # entered before, never closed
    )
    for conditions in parts:
        for entry, exit_ in session.execute(select(Visit.entry_at_utc, Visit.exit_at_utc).where(*conditions, live)):
            intervals.append((entry, exit_ or end))
    for (entry,) in session.execute(select(ActiveSession.entry_at_utc).where(ActiveSession.entry_at_utc < end)):
        intervals.append((entry, end))
    events: list[tuple[datetime, int]] = []
    for entry, exit_ in intervals:
        events.append((entry, 1))
        events.append((exit_, -1))
    events.sort()
    samples: list[tuple[datetime, int]] = []
    inside = 0
    index = 0
    moment = start
    while moment < end:
        while index < len(events) and events[index][0] <= moment:
            inside += events[index][1]
            index += 1
        samples.append((moment, inside))
        moment += step
    return samples


def occupancy(session: Session, params: ReportParams) -> ReportResult:
    samples = occupancy_samples(session, params.start, params.end)
    sums = [[0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]
    peak, peak_at = 0, params.start
    for moment, inside in samples:
        local = to_local(moment)
        sums[local.weekday()][local.hour] += inside
        counts[local.weekday()][local.hour] += 1
        if inside > peak:
            peak, peak_at = inside, moment
    matrix = [[round(sums[d][h] / counts[d][h]) if counts[d][h] else 0 for h in range(24)] for d in PERSIAN_WEEK]
    first_level = session.scalar(
        select(Level).where(Level.is_parking.is_(True), Level.is_active.is_(True)).order_by(Level.sort)
    )
    capacity = first_level.capacity if first_level else 0
    percent = round(100 * peak / capacity) if capacity else 0
    rows = [[weekday_name(day), *[fa_digits(v) for v in matrix[i]]] for i, day in enumerate(PERSIAN_WEEK)]
    return ReportResult(
        tr("report.occupancy"),
        [tr("rep.col_day"), *[fa_digits(h) for h in range(24)]],
        [Section(tr("occ.section_heatmap"), rows)],
        kpis=[
            (tr("occ.peak"), fa_digits(peak)),
            (
                tr("occ.peak_at"),
                f"{weekday_name(to_local(peak_at).weekday())} {fa_digits(to_local(peak_at).strftime('%H:%M'))}",
            ),
            (tr("occ.percent", level=first_level.code if first_level else ""), fa_digits(f"{percent}٪")),
        ],
        totals={"peak": peak, "percent": percent, "capacity": capacity},
        chart={
            "matrix": matrix,
            "rows": [weekday_name(d) for d in PERSIAN_WEEK],
            "max": max(max(r) for r in matrix) if matrix else 0,
        },
        subtitle=params.label,
        widths=[12] + [5] * 24,
    )


def _month_kpis(session: Session, params: ReportParams) -> dict[str, int]:
    entries = int(
        session.scalar(
            select(func.count())
            .select_from(EntryEvent)
            .where(EntryEvent.entry_at_utc >= params.start, EntryEvent.entry_at_utc < params.end)
        )
        or 0
    )
    avg_minutes = int(
        session.scalar(
            select(func.avg(Visit.total_minutes)).where(
                Visit.exit_at_utc >= params.start, Visit.exit_at_utc < params.end, Visit.status != "cancelled"
            )
        )
        or 0
    )
    money = financial(session, params).totals
    lost = int(
        session.scalar(
            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                Debt.created_at_utc >= params.start, Debt.created_at_utc < params.end
            )
        )
        or 0
    )
    cancelled = int(
        session.scalar(
            select(func.count())
            .select_from(Cancellation)
            .where(Cancellation.created_at_utc >= params.start, Cancellation.created_at_utc < params.end)
        )
        or 0
    )
    active_subs = int(
        session.scalar(
            select(func.count())
            .select_from(Person)
            .where(Person.kind == "subscriber", Person.is_active.is_(True), Person.subscription_end_utc >= params.end)
        )
        or 0
    )
    samples = occupancy_samples(session, params.start, params.end, timedelta(hours=2))
    peak = max((inside for _m, inside in samples), default=0)
    return {
        "entries": entries,
        "revenue": money["grand"],
        "avg_minutes": avg_minutes,
        "peak": peak,
        "debts": lost,
        "cancellations": cancelled,
        "active_subscribers": active_subs,
    }


def _change(current: int, previous: int) -> str:
    if previous == 0:
        return "—"
    percent = round(100 * (current - previous) / previous)
    return fa_digits(f"{percent:+d}٪")


def executive_summary(session: Session, params: ReportParams) -> ReportResult:
    """One page for the mall owner: this Jalali month vs the previous one (KPIs)."""
    month = jalali_of(params.start)
    start, end = jalali_month_range_utc(month.year, month.month)
    current = ReportParams(start, end)
    prev_year, prev_month = previous_month(month.year, month.month)
    prev_start, prev_end = jalali_month_range_utc(prev_year, prev_month)
    previous = ReportParams(prev_start, prev_end)
    now_values = _month_kpis(session, current)
    prev_values = _month_kpis(session, previous)
    formatters: dict[str, Callable[[int], str]] = {
        "revenue": fa_money,
        "debts": fa_money,
        "avg_minutes": fa_duration,
    }
    rows = []
    for key in ("entries", "revenue", "avg_minutes", "peak", "active_subscribers", "debts", "cancellations"):
        fmt = formatters.get(key, fa_digits)
        rows.append(
            [tr(f"exec.{key}"), fmt(now_values[key]), fmt(prev_values[key]), _change(now_values[key], prev_values[key])]
        )
    title_month = f"{jalali_month_name(month.month)} {fa_digits(month.year)}"
    return ReportResult(
        tr("report.executive"),
        [
            tr("exec.col_metric"),
            title_month,
            f"{jalali_month_name(prev_month)} {fa_digits(prev_year)}",
            tr("exec.col_change"),
        ],
        [Section("", rows)],
        kpis=[
            (tr("exec.revenue"), fa_money(now_values["revenue"])),
            (tr("exec.entries"), fa_digits(now_values["entries"])),
        ],
        totals={f"now_{k}": v for k, v in now_values.items()} | {f"prev_{k}": v for k, v in prev_values.items()},
        subtitle=title_month,
        widths=[30, 22, 22, 14],
    )
