"""Report registry (SPEC §4.12) and a runner that checks permissions.

Reports of later phases (camera accuracy 17, after-hours 18) register
themselves here when their phase lands.
"""

from __future__ import annotations

from pathlib import Path

from caspian_parking.core.jalali import format_jdate
from caspian_parking.core.permissions import Permission
from caspian_parking.i18n import tr
from caspian_parking.services.context import AppContext
from caspian_parking.services.exporters import export_excel, export_word
from caspian_parking.services.reports import ads, money, occupancy, traffic
from caspian_parking.services.reports.base import ReportDef, ReportParams, ReportResult

FINANCIAL = Permission.VIEW_FINANCIAL_REPORTS
GENERAL = Permission.VIEW_REPORTS

REPORTS: list[ReportDef] = [
    ReportDef("financial", 1, FINANCIAL, money.financial),
    ReportDef("subscriptions", 2, FINANCIAL, traffic.subscriptions),
    ReportDef("subscriber_traffic", 3, GENERAL, traffic.subscriber_traffic),
    ReportDef("plate_history", 4, GENERAL, traffic.plate_history, needs_text=True),
    ReportDef("free_access", 5, GENERAL, traffic.free_access),
    ReportDef("pass_through", 6, GENERAL, traffic.pass_through),
    ReportDef("debts", 7, FINANCIAL, money.debts),
    ReportDef("cancellations", 8, GENERAL, traffic.cancellations),
    ReportDef("lost", 9, GENERAL, traffic.lost_and_duplicates),
    ReportDef("night", 10, FINANCIAL, money.night),
    ReportDef("motorcycles", 11, GENERAL, traffic.motorcycles),
    ReportDef("occupancy", 12, GENERAL, occupancy.occupancy),
    ReportDef("executive", 13, FINANCIAL, occupancy.executive_summary),
    ReportDef("manual_changes", 14, FINANCIAL, money.manual_changes),
    ReportDef("block_attempts", 15, GENERAL, traffic.block_attempts),
    ReportDef("no_plate", 16, GENERAL, traffic.no_plate),
    ReportDef("outages", 19, GENERAL, traffic.outages),
    ReportDef("ads", 20, FINANCIAL, ads.ads),
    ReportDef("coupons", 20, FINANCIAL, ads.coupons),
    ReportDef("wallets", 20, FINANCIAL, ads.wallets),
    ReportDef("shop_performance", 20, GENERAL, ads.shop_performance, needs_text=True),
    ReportDef("open_sessions", 21, GENERAL, traffic.open_sessions),
]


class ReportError(RuntimeError):
    pass


def register(report: ReportDef) -> None:
    """Later phases add their reports (kept in SPEC order)."""
    for index, existing in enumerate(REPORTS):
        if existing.key == report.key:
            REPORTS[index] = report
            break
    else:
        REPORTS.append(report)
    REPORTS.sort(key=lambda r: r.number)


def get_report(key: str) -> ReportDef:
    for report in REPORTS:
        if report.key == key:
            return report
    raise ReportError("reports.unknown")


def available_reports(ctx: AppContext) -> list[ReportDef]:
    return [r for r in REPORTS if ctx.can(r.permission)]


def run_report(ctx: AppContext, key: str, params: ReportParams, check_permission: bool = True) -> ReportResult:
    report = get_report(key)
    if check_permission and not ctx.can(report.permission):
        raise ReportError("gate.permission_denied")
    with ctx.read() as session:
        return report.runner(session, params)


def export_report(result: ReportResult, folder: Path, kind: str, stamp_source: object = None) -> Path:
    """Write a report as Excel, Word or PDF; returns the file path."""
    stamp = format_jdate(stamp_source, sep="-") if stamp_source is not None else ""  # type: ignore[arg-type]
    name = f"{result.title}{'_' + stamp if stamp else ''}".replace("/", "-").replace(" ", "_")
    table = result.to_table()
    if kind == "word":
        return export_word(table, folder / f"{name}.docx")
    if kind == "pdf":
        from caspian_parking.services.pdf import export_pdf

        return export_pdf(table, folder / f"{name}.pdf", chart=result.chart)
    if kind != "excel":
        raise ReportError(tr("reports.unknown_format"))
    return export_excel(table, folder / f"{name}.xlsx")


__all__ = [
    "REPORTS",
    "ReportDef",
    "ReportError",
    "ReportParams",
    "ReportResult",
    "available_reports",
    "export_report",
    "get_report",
    "register",
    "run_report",
]
