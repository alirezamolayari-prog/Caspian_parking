"""Multi-gate sync (SPEC §2.2): outbox on gates, sequence log on the server, review queue, cross-gate
session aliases, morning acknowledgements and the shared ticket key."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Date, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.core.ids import uuid7
from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime

TABLE_NAME_LENGTH = 60


class SyncOutbox(Base):
    """Gate side: rows written here that the server has not received yet (a technical queue)."""

    __tablename__ = "sync_outbox"
    __table_args__ = (Index("ix_sync_outbox_row", "table_name", "row_id"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True, default=uuid7)
    table_name: Mapped[str] = mapped_column(String(TABLE_NAME_LENGTH))
    row_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    queued_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())


class SyncState(Base):
    """Small key/value store of this database's sync progress (pull cursor, last sync, joined server)."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Unicode(400))


class SyncLog(Base):
    """Server side: every row written to the central database, in commit order (gates pull by ``seq``)."""

    __tablename__ = "sync_log"
    __table_args__ = (Index("ix_sync_log_row", "table_name", "row_id"),)

    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    table_name: Mapped[str] = mapped_column(String(TABLE_NAME_LENGTH))
    row_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    writer_node: Mapped[str] = mapped_column(String(ID_LENGTH))
    logged_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())


class SharedSecret(Base):
    """Installation-wide secrets distributed to gates when they join (the ticket HMAC key). Never synced."""

    __tablename__ = "shared_secrets"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class ReviewItem(ReferenceMixin, Base):
    """'Needs review' queue: possible duplicates entered offline, sync conflicts. Never auto-resolved."""

    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_items_status", "status"),)

    kind: Mapped[str] = mapped_column(String(30))  # duplicate_payment | duplicate_deposit | conflict
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    summary: Mapped[str] = mapped_column(Unicode(400), default="")
    refs: Mapped[dict[str, Any]] = mapped_column(JSONText(), default=dict)
    resolution: Mapped[str | None] = mapped_column(Unicode(400), default=None)


class SessionAlias(EventMixin, Base):
    """A gate closed another gate's ticket while offline under a provisional session; this links the two."""

    __tablename__ = "session_aliases"
    __table_args__ = (
        Index("ix_session_aliases_provisional", "provisional_session_id"),
        Index("ix_session_aliases_session", "session_id"),
    )

    provisional_session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))


class MorningAck(EventMixin, Base):
    """First user of the day acknowledged the after-hours (watch mode) report."""

    __tablename__ = "morning_acks"
    __table_args__ = (Index("ix_morning_acks_day", "day"),)
    __append_only__: ClassVar[bool] = True

    day: Mapped[date] = mapped_column(Date)
    passes: Mapped[int] = mapped_column(Integer, default=0)
