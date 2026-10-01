"""After-hours watch mode (SPEC §4.10): outside working hours every camera pass is logged with its photo,
no receipts are printed, blocked plates are highlighted, and the first user of the morning acknowledges
the night's report (who / when are stored)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import local_date, start_of_local_day_utc
from caspian_parking.data.models import CameraRead, MorningAck
from caspian_parking.services.blocklist import BlocklistService
from caspian_parking.services.context import AppContext
from caspian_parking.services.tariff_service import load_calendar

LOOKBACK_DAYS = 7


def is_working(session: Session, now: datetime) -> bool:
    window = load_calendar(session).opening_utc(local_date(now))
    return window is not None and window[0] <= now < window[1]


def night_window(session: Session, day: date) -> tuple[datetime, datetime]:
    """From the last closing before ``day`` to ``day``'s opening (or the end of ``day`` when closed)."""
    calendar = load_calendar(session)
    today = calendar.opening_utc(day)
    end = today[0] if today else start_of_local_day_utc(day + timedelta(days=1))
    start = start_of_local_day_utc(day - timedelta(days=1))
    for back in range(1, LOOKBACK_DAYS + 1):
        previous = calendar.opening_utc(day - timedelta(days=back))
        if previous is not None:
            start = previous[1]
            break
    return start, end


@dataclass(frozen=True)
class NightPass:
    read: CameraRead
    blocked: bool


@dataclass(frozen=True)
class MorningReport:
    day: date
    start: datetime
    end: datetime
    passes: list[NightPass]

    @property
    def blocked_count(self) -> int:
        return sum(1 for p in self.passes if p.blocked)


class WatchService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def after_hours_now(self) -> bool:
        with self.ctx.read() as session:
            return not is_working(session, self.ctx.clock.now_utc())

    def night_report(self, day: date | None = None) -> MorningReport:
        day = day or local_date(self.ctx.clock.now_utc())
        blocklist = BlocklistService(self.ctx)
        with self.ctx.read() as session:
            start, end = night_window(session, day)
            reads = session.scalars(
                select(CameraRead)
                .where(
                    CameraRead.after_hours.is_(True),
                    CameraRead.created_at_utc >= start,
                    CameraRead.created_at_utc < end,
                )
                .order_by(CameraRead.created_at_utc)
            ).all()
            passes = [
                NightPass(r, bool(r.plate_key and blocklist.match(session, r.plate_key) is not None)) for r in reads
            ]
        return MorningReport(day, start, end, passes)

    def acknowledged(self, day: date) -> MorningAck | None:
        with self.ctx.read() as session:
            return session.scalar(select(MorningAck).where(MorningAck.day == day).order_by(MorningAck.created_at_utc))

    def pending_report(self) -> MorningReport | None:
        """The night report to show at the first login of the day (None if empty or already acknowledged)."""
        now = self.ctx.clock.now_utc()
        day = local_date(now)
        if self.acknowledged(day) is not None:
            return None
        report = self.night_report(day)
        if not report.passes or now < report.end:
            return None
        return report

    def acknowledge(self, report: MorningReport) -> MorningAck:
        with self.ctx.uow() as session:
            ack = MorningAck(day=report.day, passes=len(report.passes))
            session.add(ack)
        return ack

    def count_after_hours(self, start: datetime, end: datetime) -> int:
        with self.ctx.read() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(CameraRead)
                    .where(
                        CameraRead.after_hours.is_(True),
                        CameraRead.created_at_utc >= start,
                        CameraRead.created_at_utc < end,
                    )
                )
                or 0
            )
