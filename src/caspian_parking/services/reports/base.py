"""Report framework: parameters → result (sections, totals, KPIs, chart data) → exports.

Every report is a pure function of a read session and the parameters, so it can run in a worker
thread (never on the UI thread) and on the server database later (Phase 9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from caspian_parking.core.jalali import (
    JalaliDate,
    jalali_month_range_utc,
    jalali_of,
    local_date,
    local_day_range_utc,
    start_of_local_day_utc,
)
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_date
from caspian_parking.services.exporters import ExportTable, Section


@dataclass(frozen=True)
class ReportParams:
    start: datetime  # UTC, inclusive
    end: datetime  # UTC, exclusive
    gate_code: int | None = None
    operator_id: str | None = None
    category: str | None = None
    text: str = ""  # free search (plate for the plate history report)

    @property
    def label(self) -> str:
        last_day = local_date(self.end - timedelta(seconds=1))
        first_day = local_date(self.start)
        if first_day == last_day:
            return fa_date(first_day)
        return tr("reports.range", start=fa_date(first_day), end=fa_date(last_day))


def day_params(day: date, **kwargs: Any) -> ReportParams:
    start, end = local_day_range_utc(day)
    return ReportParams(start, end, **kwargs)


def month_params(year: int, month: int, **kwargs: Any) -> ReportParams:
    start, end = jalali_month_range_utc(year, month)
    return ReportParams(start, end, **kwargs)


def this_month_params(now: datetime, **kwargs: Any) -> ReportParams:
    today = jalali_of(now)
    return month_params(today.year, today.month, **kwargs)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def range_params(first: date, last: date, **kwargs: Any) -> ReportParams:
    return ReportParams(start_of_local_day_utc(first), start_of_local_day_utc(last + timedelta(days=1)), **kwargs)


@dataclass
class ReportResult:
    title: str
    columns: list[str]
    sections: list[Section] = field(default_factory=list)
    kpis: list[tuple[str, str]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    chart: dict[str, Any] | None = None
    subtitle: str = ""
    local_only: bool = False
    widths: list[int] = field(default_factory=list)

    def row_count(self) -> int:
        return sum(len(s.rows) for s in self.sections)

    def to_table(self) -> ExportTable:
        subtitle = self.subtitle
        if self.kpis:
            subtitle += ("\n" if subtitle else "") + " — ".join(f"{k}: {v}" for k, v in self.kpis)
        footer = tr("reports.local_banner") if self.local_only else ""
        return ExportTable(
            self.title, self.columns, self.sections, subtitle=subtitle, widths=self.widths, footer=footer
        )


Runner = Callable[[Session, ReportParams], ReportResult]


@dataclass(frozen=True)
class ReportDef:
    key: str
    number: int  # SPEC §4.12 numbering
    permission: str
    runner: Runner
    needs_text: bool = False

    @property
    def title(self) -> str:
        return tr(f"report.{self.key}")


def jdate_text(value: datetime | date | None) -> str:
    return fa_date(value) if value is not None else "—"


def jalali_today(now: datetime) -> JalaliDate:
    return jalali_of(now)
