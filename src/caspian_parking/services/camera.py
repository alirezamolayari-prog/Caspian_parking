"""Camera reads (SPEC §4.2–4.4, §4.10): store every pass with its photo, match it to the parking session,
keep corrections (camera read + corrected value), unidentified passes, accuracy statistics (report 17)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from caspian_parking.core.anpr import confidence_percent
from caspian_parking.core.plate import Plate
from caspian_parking.data.models import CameraRead, Gate, Photo, PlateCorrection, ReadMatch
from caspian_parking.devices.plate_source import PlatePass
from caspian_parking.services import photos
from caspian_parking.services.context import AppContext
from caspian_parking.services.people import PeopleService

UNIDENTIFIED_DAYS = 7
DIRECTION = {"entry": "in", "exit": "out"}


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraAccuracy:
    camera: str
    lane: str
    reads: int
    matched: int
    corrected: int
    unidentified: int

    @property
    def accuracy_percent(self) -> int | None:
        """Matched reads that needed no correction (None when nothing was matched yet)."""
        if not self.matched:
            return None
        return round(100 * (self.matched - self.corrected) / self.matched)


def _gate_name(session: Session, code: int | None) -> str:
    gate = session.scalar(select(Gate).where(Gate.code == code)) if code is not None else None
    return gate.name if gate is not None else str(code or "")


class CameraService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def record_pass(self, item: PlatePass, after_hours: bool = False) -> CameraRead:
        """Store the pass (photo first, so the read can point to it)."""
        plate = item.result.plate
        gate_code = self.ctx.config.gate_code
        photo_id = None
        if item.snapshot:
            with self.ctx.read() as session:
                gate_name = _gate_name(session, gate_code)
                person = PeopleService(self.ctx).person_for_plate(session, plate.key) if plate else None
            category = "transient" if person is None else person.kind
            name = photos.photo_filename(
                item.at,
                DIRECTION.get(item.lane, "in"),
                gate_name,
                plate.text if plate else None,
                category,
                person.full_name if person else None,
                person.vehicle_model if person else None,
            )
            photo_id = photos.store_photo(self.ctx, item.snapshot, item.at, item.lane, name).id
        with self.ctx.uow() as session:
            read = CameraRead(
                gate_code=gate_code,
                lane=item.lane,
                camera=item.camera,
                plate_key=plate.key if plate else None,
                confidence=confidence_percent(item.result.confidence),
                accepted=item.result.accepted,
                vehicle_type=item.result.vehicle_type,
                frames=item.result.frames,
                photo_id=photo_id,
                after_hours=after_hours,
            )
            session.add(read)
        return read

    def link(self, read_id: str, session_id: str, plate: Plate | None, vehicle_type: str | None = None) -> None:
        """The read belongs to this session; if the operator changed the plate, keep both values."""
        with self.ctx.uow() as session:
            read = session.get(CameraRead, read_id)
            if read is None:
                raise CameraError("camera.read_not_found")
            session.add(ReadMatch(read_id=read.id, session_id=session_id, lane=read.lane))
            final_key = plate.key if plate is not None else None
            type_changed = (
                vehicle_type is not None and read.vehicle_type is not None and vehicle_type != read.vehicle_type
            )
            if final_key is not None and (final_key != read.plate_key or type_changed):
                session.add(
                    PlateCorrection(
                        read_id=read.id,
                        session_id=session_id,
                        read_plate_key=read.plate_key,
                        corrected_plate_key=final_key,
                        corrected_vehicle_type=vehicle_type if type_changed else None,
                    )
                )
            if read.photo_id:
                photo = session.get(Photo, read.photo_id)
                if photo is not None and photo.session_id is None:
                    photo.session_id = session_id

    def fill_unidentified(self, read_id: str, plate: Plate) -> PlateCorrection:
        """Operator fills the plate of a pass the camera could not read."""
        with self.ctx.uow() as session:
            read = session.get(CameraRead, read_id)
            if read is None:
                raise CameraError("camera.read_not_found")
            correction = PlateCorrection(read_id=read.id, read_plate_key=read.plate_key, corrected_plate_key=plate.key)
            session.add(correction)
        return correction

    def unidentified(self, days: int = UNIDENTIFIED_DAYS) -> list[CameraRead]:
        """Passes without a readable plate that nobody has fixed or matched yet (newest first)."""
        since = self.ctx.clock.now_utc() - timedelta(days=days)
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(CameraRead)
                    .where(
                        CameraRead.plate_key.is_(None),
                        CameraRead.created_at_utc >= since,
                        CameraRead.id.not_in(select(PlateCorrection.read_id)),
                        CameraRead.id.not_in(select(ReadMatch.read_id)),
                    )
                    .order_by(CameraRead.created_at_utc.desc())
                )
            )

    def photo_path(self, read: CameraRead) -> str | None:
        if not read.photo_id:
            return None
        with self.ctx.read() as session:
            photo = session.get(Photo, read.photo_id)
            return photo.path if photo is not None and photo.file_deleted_at_utc is None else None


def accuracy(session: Session, start: datetime, end: datetime) -> list[CameraAccuracy]:
    """Per camera: reads, reads matched to a session, matched reads the operator corrected, unreadable passes."""
    in_range = (CameraRead.created_at_utc >= start, CameraRead.created_at_utc < end)
    totals = {
        (cam, lane): (int(total), int(unreadable or 0))
        for cam, lane, total, unreadable in session.execute(
            select(
                CameraRead.camera,
                CameraRead.lane,
                func.count(),
                func.sum(case((CameraRead.plate_key.is_(None), 1), else_=0)),
            )
            .where(*in_range)
            .group_by(CameraRead.camera, CameraRead.lane)
        )
    }
    matched_reads = select(ReadMatch.read_id).distinct()
    matched = {
        (cam, lane): int(count)
        for cam, lane, count in session.execute(
            select(CameraRead.camera, CameraRead.lane, func.count())
            .where(*in_range, CameraRead.id.in_(matched_reads))
            .group_by(CameraRead.camera, CameraRead.lane)
        )
    }
    corrected_reads = select(PlateCorrection.read_id).where(PlateCorrection.session_id.is_not(None)).distinct()
    corrected = {
        (cam, lane): int(count)
        for cam, lane, count in session.execute(
            select(CameraRead.camera, CameraRead.lane, func.count())
            .where(*in_range, CameraRead.id.in_(matched_reads), CameraRead.id.in_(corrected_reads))
            .group_by(CameraRead.camera, CameraRead.lane)
        )
    }
    return [
        CameraAccuracy(cam, lane, total, matched.get((cam, lane), 0), corrected.get((cam, lane), 0), unreadable)
        for (cam, lane), (total, unreadable) in sorted(totals.items())
    ]
