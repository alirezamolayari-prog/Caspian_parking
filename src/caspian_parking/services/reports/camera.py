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
