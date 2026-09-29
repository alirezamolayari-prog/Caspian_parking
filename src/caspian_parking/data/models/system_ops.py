"""Fiscal years, stored photos and power-outage log (SPEC §4.13, §2.5)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import Boolean, Date, Index, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime


class FiscalYear(ReferenceMixin, Base):
    """A fiscal year. Closing archives the counters and makes the year read-only."""

    __tablename__ = "fiscal_years"

    name: Mapped[str] = mapped_column(Unicode(40))
    start_date: Mapped[date] = mapped_column(Date, unique=True)  # Tehran calendar date
    end_date: Mapped[date] = mapped_column(Date)  # inclusive
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed
    closed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    closed_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    final_counters: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), default=None)


class Photo(ReferenceMixin, Base):
    """Camera photo stored as a file; only the path is in the database (SPEC §2.4)."""

    __tablename__ = "photos"
    __audited__: ClassVar[bool] = False  # thousands per day; the file itself is the record
    __table_args__ = (Index("ix_photos_taken", "taken_at_utc"), Index("ix_photos_session", "session_id"))

    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    kind: Mapped[str] = mapped_column(String(20))  # entry | exit | block | unidentified | after_hours
    path: Mapped[str] = mapped_column(Unicode(400))
    taken_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    keep_forever: Mapped[bool] = mapped_column(Boolean, default=False)
    file_deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)


class OutageEvent(EventMixin, Base):
    """Gap in the heartbeat: the PC (or the app) was off from ``started_at`` to ``ended_at``."""

    __tablename__ = "outage_events"
    __table_args__ = (Index("ix_outage_events_started", "started_at_utc"),)

    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    clean_shutdown: Mapped[bool] = mapped_column(Boolean, default=False)
