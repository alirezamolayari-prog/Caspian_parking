"""Tariff versions and holidays (reference data, audited)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Index, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, ReferenceMixin
from caspian_parking.data.types import JSONText, UTCDateTime


class TariffVersionRecord(ReferenceMixin, Base):
    """One version of all tariff values, in force from ``effective_from_utc`` (history is kept)."""

    __tablename__ = "tariff_versions"
    __table_args__ = (Index("ix_tariff_versions_effective", "effective_from_utc"),)

    effective_from_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    amounts: Mapped[dict[str, Any]] = mapped_column(JSONText())  # TariffValues as JSON
    note: Mapped[str | None] = mapped_column(Unicode(300), default=None)


class Holiday(ReferenceMixin, Base):
    """Manual holiday (a free day), stored as the Tehran calendar date."""

    __tablename__ = "holidays"

    day: Mapped[date] = mapped_column(Date, unique=True)
    title: Mapped[str] = mapped_column(Unicode(120))
