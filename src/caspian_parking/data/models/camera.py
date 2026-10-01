"""Camera reads (SPEC §4.2–4.4, §4.10, report 17): every pass seen by a camera, its link to a parking
session, and manual corrections. All append-only: a correction or a match is a new event."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin
from caspian_parking.data.models.gate import PLATE_KEY_LENGTH
from caspian_parking.data.types import ID_LENGTH


class CameraRead(EventMixin, Base):
    """One vehicle pass seen by a camera (after multi-frame voting). ``plate_key`` None = unreadable."""

    __tablename__ = "camera_reads"
    __table_args__ = (
        Index("ix_camera_reads_created", "created_at_utc"),
        Index("ix_camera_reads_plate", "plate_key"),
    )

    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
    lane: Mapped[str] = mapped_column(String(10))  # entry | exit
    camera: Mapped[str] = mapped_column(Unicode(60))
    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # percent 0..100
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    vehicle_type: Mapped[str | None] = mapped_column(String(20), default=None)
    frames: Mapped[int] = mapped_column(Integer, default=1)
    photo_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    after_hours: Mapped[bool] = mapped_column(Boolean, default=False)  # watch mode (SPEC §4.10)


class ReadMatch(EventMixin, Base):
    """A camera read belongs to a parking session (entry registered / exit completed)."""

    __tablename__ = "read_matches"
    __table_args__ = (Index("ix_read_matches_read", "read_id"), Index("ix_read_matches_session", "session_id"))

    read_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    lane: Mapped[str] = mapped_column(String(10))


class PlateCorrection(EventMixin, Base):
    """The operator's plate differs from the camera read (or fills an unidentified pass)."""

    __tablename__ = "plate_corrections"
    __table_args__ = (Index("ix_plate_corrections_read", "read_id"),)

    read_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    read_plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    corrected_plate_key: Mapped[str] = mapped_column(Unicode(PLATE_KEY_LENGTH))
    corrected_vehicle_type: Mapped[str | None] = mapped_column(String(20), default=None)
