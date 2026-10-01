"""Hardware records (SPEC §6): barrier openings (with the reason) and RFID cards linked to people."""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.types import ID_LENGTH


class BarrierOpen(EventMixin, Base):
    """Every barrier opening: automatic (paid, subscriber, free…) or manual with the operator's reason."""

    __tablename__ = "barrier_opens"
    __table_args__ = (Index("ix_barrier_opens_created", "created_at_utc"),)

    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
    lane: Mapped[str] = mapped_column(String(10))  # entry | exit
    cause: Mapped[str] = mapped_column(String(20))  # receipt | subscriber | free | card | paid | free_exit | manual
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    reason: Mapped[str | None] = mapped_column(Unicode(300), default=None)


class PersonCard(ReferenceMixin, Base):
    """An RFID / UHF card of a subscriber or free-access person. A lost card is deactivated (with reason)."""

    __tablename__ = "person_cards"
    __table_args__ = (Index("ix_person_cards_person", "person_id"),)

    person_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    uid: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(Unicode(60), default="")
