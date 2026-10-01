"""Report 17 (SPEC §4.12): camera accuracy — manual corrections vs camera reads."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from caspian_parking.data.models import CameraRead, PlateCorrection
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_digits
from caspian_parking.services.camera import accuracy
from caspian_parking.services.exporters import Section
from caspian_parking.services.reports.base import ReportParams, ReportResult
from caspian_parking.services.reports.common import plate_cell


def camera_accuracy(session: Session, params: ReportParams) -> ReportResult:
    stats = accuracy(session, params.start, params.end)
    rows: list[list[object]] = [
        [
            s.camera,
            tr(f"camera.lane_{s.lane}"),
            s.reads,
            s.matched,
            s.corrected,
            s.unidentified,
            fa_digits(f"{s.accuracy_percent}٪") if s.accuracy_percent is not None else "—",
        ]
        for s in stats
    ]
    corrections = []
    for correction, read in session.execute(
        select(PlateCorrection, CameraRead)
        .join(CameraRead, CameraRead.id == PlateCorrection.read_id)
        .where(PlateCorrection.created_at_utc >= params.start, PlateCorrection.created_at_utc < params.end)
        .order_by(PlateCorrection.created_at_utc)
    ):
        corrections.append(
            [
                read.camera,
                tr(f"camera.lane_{read.lane}"),
                fa_datetime(correction.created_at_utc),
                plate_cell(read.plate_key) if read.plate_key else tr("camera.unreadable"),
                plate_cell(correction.corrected_plate_key),
                fa_digits(f"{read.confidence}٪"),
                "",
            ]
        )
    matched = sum(s.matched for s in stats)
    corrected = sum(s.corrected for s in stats)
    overall = round(100 * (matched - corrected) / matched) if matched else None
    return ReportResult(
        tr("report.camera_accuracy"),
        [
            tr("camera.col_camera"),
            tr("camera.col_lane"),
            tr("camera.col_reads"),
            tr("camera.col_matched"),
            tr("camera.col_corrected"),
            tr("camera.col_unreadable"),
            tr("camera.col_accuracy"),
        ],
        [Section(tr("camera.section_summary"), rows), Section(tr("camera.section_corrections"), corrections)],
        kpis=[
            (tr("camera.col_reads"), fa_digits(sum(s.reads for s in stats))),
            (tr("camera.col_accuracy"), fa_digits(f"{overall}٪") if overall is not None else "—"),
        ],
        totals={
            "reads": sum(s.reads for s in stats),
            "matched": matched,
            "corrected": corrected,
            "unidentified": sum(s.unidentified for s in stats),
        },
        subtitle=params.label,
        widths=[18, 12, 18, 14, 14, 12, 12],
    )


def after_hours(session: Session, params: ReportParams) -> ReportResult:
    """Report 18: traffic outside working hours — watch-mode camera passes and receipts issued after hours."""
    from caspian_parking.data.models import EntryEvent
    from caspian_parking.services.watch import is_working

    passes = []
    blocked = 0
    for read in session.scalars(
        select(CameraRead)
        .where(
            CameraRead.after_hours.is_(True),
            CameraRead.created_at_utc >= params.start,
            CameraRead.created_at_utc < params.end,
        )
        .order_by(CameraRead.created_at_utc)
    ):
        is_blocked = bool(read.plate_key and _blocked(session, read.plate_key))
        blocked += is_blocked
        passes.append(
            [
                fa_datetime(read.created_at_utc),
                tr(f"camera.lane_{read.lane}"),
                plate_cell(read.plate_key) if read.plate_key else tr("camera.unreadable"),
                tr("watch.blocked") if is_blocked else "",
            ]
        )
    receipts = [
        [fa_datetime(e.entry_at_utc), tr("camera.lane_entry"), plate_cell(e.plate_key), e.ticket_no]
        for e in session.scalars(
            select(EntryEvent)
            .where(EntryEvent.entry_at_utc >= params.start, EntryEvent.entry_at_utc < params.end)
            .order_by(EntryEvent.entry_at_utc)
        )
        if not is_working(session, e.entry_at_utc)
    ]
    return ReportResult(
        tr("report.after_hours"),
        [tr("review.col_when"), tr("camera.col_lane"), tr("rep.col_plate"), tr("watch.col_note")],
        [Section(tr("watch.section_passes"), passes), Section(tr("watch.section_receipts"), receipts)],
        kpis=[
            (tr("watch.kpi_passes"), fa_digits(len(passes))),
            (tr("watch.blocked"), fa_digits(blocked)),
        ],
        totals={"passes": len(passes), "blocked": blocked, "receipts": len(receipts)},
        subtitle=params.label,
        widths=[22, 12, 22, 20],
    )


def _blocked(session: Session, plate_key: str) -> bool:
    """Blocked directly or through the person who owns the plate (same rule as the gate)."""
    from caspian_parking.data.models import Block, PersonPlate

    owners = select(PersonPlate.person_id).where(PersonPlate.plate_key == plate_key, PersonPlate.is_active.is_(True))
    stmt = select(Block.id).where(
        Block.is_active.is_(True), (Block.plate_key == plate_key) | Block.person_id.in_(owners)
    )
    return session.scalar(stmt.limit(1)) is not None
