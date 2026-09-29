"""Background jobs (SQL Server Express has no Agent, so the app schedules everything itself).

* automatic daily report at a configurable time, with catch-up at the next start when the PC was off
* automatic renewal of shop-paid subscriptions from wallets (when enabled)

Jobs run on APScheduler's worker thread and only use the database (never Qt widgets).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

from caspian_parking.core.jalali import TEHRAN, JalaliDate, to_local
from caspian_parking.services.context import AppContext
from caspian_parking.services.reports import export_report, run_report
from caspian_parking.services.reports.base import day_params
from caspian_parking.services.settings import get_setting, set_setting

log = logging.getLogger(__name__)

DAILY_REPORTS = ("financial", "open_sessions", "night", "debts")
MAX_CATCH_UP_DAYS = 7


def daily_folder(ctx: AppContext, day: date) -> Path:
    with ctx.read() as session:
        custom = str(get_setting(session, "reports.daily_folder") or "")
    base = Path(custom) if custom else ctx.data_root.reports
    jalali = JalaliDate.from_gregorian(day)
    return base / f"{jalali.year:04d}" / f"{jalali.month:02d}"


def generate_daily_report(ctx: AppContext, day: date) -> list[Path]:
    """Write the daily reports of ``day`` (Excel + Word) and remember the day as done."""
    folder = daily_folder(ctx, day)
    with ctx.read() as session:
        formats = list(get_setting(session, "reports.daily_formats"))
    stamp = datetime.combine(day, time(12), tzinfo=TEHRAN)
    files: list[Path] = []
    for key in DAILY_REPORTS:
        result = run_report(ctx, key, day_params(day), check_permission=False)
        for kind in formats:
            files.append(export_report(result, folder, kind, stamp_source=stamp))
    with ctx.uow(reason="daily report") as session:
        last = str(get_setting(session, "reports.daily_last") or "")
        if not last or last < day.isoformat():
            set_setting(session, "reports.daily_last", day.isoformat())
    log.info("daily report for %s written (%d files)", day, len(files))
    return files


def _daily_time(ctx: AppContext) -> time:
    with ctx.read() as session:
        return time.fromisoformat(str(get_setting(session, "reports.daily_time")))


def due_days(ctx: AppContext, now: datetime) -> list[date]:
    """Days whose report time has passed but whose report was not written yet (oldest first)."""
    with ctx.read() as session:
        if not get_setting(session, "reports.daily_enabled"):
            return []
        last_text = str(get_setting(session, "reports.daily_last") or "")
    local_now = to_local(now)
    latest = local_now.date() if local_now.time() >= _daily_time(ctx) else local_now.date() - timedelta(days=1)
    first = latest - timedelta(days=MAX_CATCH_UP_DAYS - 1)
    if last_text:
        first = max(first, date.fromisoformat(last_text) + timedelta(days=1))
    days = []
    day = first
    while day <= latest:
        days.append(day)
        day += timedelta(days=1)
    return days


def catch_up(ctx: AppContext) -> list[date]:
    done = []
    for day in due_days(ctx, ctx.clock.now_utc()):
        try:
            generate_daily_report(ctx, day)
            done.append(day)
        except Exception:  # pragma: no cover - logged, retried at the next run
            log.exception("daily report for %s failed", day)
            break
    return done


def renew_wallets(ctx: AppContext) -> int:
    from caspian_parking.services.people import PeopleService

    with ctx.read() as session:
        if not get_setting(session, "wallet.auto_renew"):
            return 0
    return len(PeopleService(ctx).renew_due_from_wallets(system=True))


class AppScheduler:
    """Owns the APScheduler instance of this process."""

    def __init__(self, ctx: AppContext) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self.ctx = ctx
        self.scheduler = BackgroundScheduler(timezone=TEHRAN)

    def start(self) -> None:
        daily = _daily_time(self.ctx)
        self.scheduler.add_job(
            catch_up, "cron", args=[self.ctx], hour=daily.hour, minute=daily.minute, id="daily_report"
        )
        self.scheduler.add_job(renew_wallets, "cron", args=[self.ctx], hour=8, minute=0, id="wallet_renewal")
        self.scheduler.add_job(catch_up, "date", args=[self.ctx], id="daily_catch_up")  # right after start
        self.scheduler.start()
        log.info("scheduler started (daily report at %s)", daily)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def job_ids(self) -> list[str]:
        return [job.id for job in self.scheduler.get_jobs()]


__all__ = ["AppScheduler", "catch_up", "due_days", "generate_daily_report", "renew_wallets"]
