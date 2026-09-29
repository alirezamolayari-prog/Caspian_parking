"""Gate operations: live sessions, append-only traffic/money events and the visit history.

* ``active_sessions`` — vehicles currently inside (a few hundred rows; the operator screen only reads this)
* ``*_events`` / ``payments`` / … — append-only facts (never updated or deleted, DB triggers enforce it)
* ``visits`` — one row per finished stay, a projection of the events for fast history/report queries
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Boolean, Date, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EntityMixin, EventMixin
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime

PLATE_KEY_LENGTH = 40
TICKET_NO_LENGTH = 24


class GateSequence(Base):
    """Last ticket sequence issued by a gate (local state of that gate; never reused)."""

    __tablename__ = "gate_sequences"

    gate_code: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class _SessionFields:
    gate_code: Mapped[int] = mapped_column(Integer)
    ticket_sequence: Mapped[int] = mapped_column(BigInteger)
    ticket_no: Mapped[str] = mapped_column(String(TICKET_NO_LENGTH))
    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    vehicle_type: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    pass_type: Mapped[str | None] = mapped_column(String(20), default=None)
    category: Mapped[str] = mapped_column(String(20))
    entry_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    entry_minute: Mapped[int] = mapped_column(BigInteger)
    no_plate: Mapped[bool] = mapped_column(Boolean, default=False)


class ActiveSession(_SessionFields, EntityMixin, Base):
    """Projection of vehicles inside. Rows are removed at exit/cancel (the events keep the history)."""

    __tablename__ = "active_sessions"
    __deletable__: ClassVar[bool] = True
    __table_args__ = (
        Index("ix_active_sessions_plate", "plate_key"),
        Index("ix_active_sessions_ticket", "gate_code", "ticket_sequence"),
        Index("ix_active_sessions_minute", "gate_code", "entry_minute"),
        Index("ix_active_sessions_entry", "entry_at_utc"),
    )

    night_marked: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list[str]] = mapped_column(JSONText(), default=list)


class EntryEvent(_SessionFields, EventMixin, Base):
    __tablename__ = "entry_events"
    __table_args__ = (
        Index("ix_entry_events_session", "session_id"),
        Index("ix_entry_events_entry", "entry_at_utc"),
        Index("ix_entry_events_plate", "plate_key"),
        Index("ix_entry_events_ticket", "gate_code", "ticket_sequence"),
    )

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))


class ExitEvent(EventMixin, Base):
    __tablename__ = "exit_events"
    __table_args__ = (Index("ix_exit_events_session", "session_id"), Index("ix_exit_events_exit", "exit_at_utc"))

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    gate_code: Mapped[int] = mapped_column(Integer)
    exit_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20))  # paid | free | fled
    total_minutes: Mapped[int] = mapped_column(BigInteger)
    chargeable_minutes: Mapped[int] = mapped_column(BigInteger)
    amount_due: Mapped[int] = mapped_column(BigInteger)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONText())
    lost_ticket: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list[str]] = mapped_column(JSONText(), default=list)


class Payment(EventMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_session", "session_id"), Index("ix_payments_created", "created_at_utc"))

    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    debt_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    purpose: Mapped[str] = mapped_column(String(20))  # parking | debt
    method: Mapped[str] = mapped_column(String(20))  # cash | card | mall_card
    amount: Mapped[int] = mapped_column(BigInteger)
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
    reference: Mapped[str | None] = mapped_column(Unicode(60), default=None)


class Adjustment(EventMixin, Base):
    """Manual change of an amount (always with a reason; SPEC §4.1 'manual amount changes')."""

    __tablename__ = "adjustments"
    __table_args__ = (Index("ix_adjustments_session", "session_id"),)

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    kind: Mapped[str] = mapped_column(String(30))  # manual_amount | night_fine
    amount_before: Mapped[int] = mapped_column(BigInteger)
    amount_after: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Unicode(500))


class Cancellation(EventMixin, Base):
    """ابطال — the original record stays; this event voids it (SPEC §4.1)."""

    __tablename__ = "cancellations"
    __table_args__ = (Index("ix_cancellations_target", "target_table", "target_id"),)

    target_table: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    reason: Mapped[str] = mapped_column(Unicode(500))


class Debt(EventMixin, Base):
    """Unpaid amount of a vehicle that left without paying (فرار)."""

    __tablename__ = "debts"
    __table_args__ = (Index("ix_debts_plate", "plate_key"), Index("ix_debts_session", "session_id"))

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    amount: Mapped[int] = mapped_column(BigInteger)
    gate_code: Mapped[int] = mapped_column(Integer)


class Reprint(EventMixin, Base):
    """Duplicate (المثنی) receipt printed for a session."""

    __tablename__ = "reprints"
    __table_args__ = (Index("ix_reprints_session", "session_id"),)

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    gate_code: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="duplicate")


class NightMark(EventMixin, Base):
    """Vehicle marked as night parking (by an operator, or automatically the next morning)."""

    __tablename__ = "night_marks"
    __table_args__ = (Index("ix_night_marks_session", "session_id"),)

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    night_of: Mapped[date] = mapped_column(Date)
    auto: Mapped[bool] = mapped_column(Boolean, default=False)


class Visit(EntityMixin, Base):
    """One finished (or cancelled) stay — projection for history, plate search and reports."""

    __tablename__ = "visits"
    __table_args__ = (
        Index("ix_visits_session", "session_id", unique=True),
        Index("ix_visits_plate", "plate_key"),
        Index("ix_visits_entry", "entry_at_utc"),
        Index("ix_visits_exit", "exit_at_utc"),
        Index("ix_visits_status", "status"),
    )

    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    ticket_no: Mapped[str] = mapped_column(String(TICKET_NO_LENGTH))
    gate_in: Mapped[int] = mapped_column(Integer)
    gate_out: Mapped[int | None] = mapped_column(Integer, default=None)
    vehicle_type: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(20))
    entry_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    exit_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    total_minutes: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_due: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_paid: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20))  # paid | free | fled | recovered | cancelled
    lost_ticket: Mapped[bool] = mapped_column(Boolean, default=False)
    no_plate: Mapped[bool] = mapped_column(Boolean, default=False)
    night_count: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list[str]] = mapped_column(JSONText(), default=list)
