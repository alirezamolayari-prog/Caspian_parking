"""System tables: nodes, gates, levels, settings, users, role presets, audit log, shifts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Boolean, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime


class Node(ReferenceMixin, Base):
    """A PC taking part in the installation (server, gate or standalone)."""

    __tablename__ = "nodes"

    name: Mapped[str] = mapped_column(Unicode(100))
    role: Mapped[str] = mapped_column(String(20))
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
    last_seen_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)


class Gate(ReferenceMixin, Base):
    __tablename__ = "gates"

    code: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(Unicode(100))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Level(ReferenceMixin, Base):
    """Parking level (P1, P2 …) with a configurable capacity."""

    __tablename__ = "levels"

    code: Mapped[str] = mapped_column(Unicode(20), unique=True)
    name: Mapped[str] = mapped_column(Unicode(100))
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    is_parking: Mapped[bool] = mapped_column(Boolean, default=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Setting(ReferenceMixin, Base):
    """Installation-wide setting (value is JSON)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), unique=True)
    value: Mapped[Any] = mapped_column(JSONText())


class RolePreset(ReferenceMixin, Base):
    """Named set of permissions used to fill a user's permission matrix quickly."""

    __tablename__ = "role_presets"

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(Unicode(100))
    permissions: Mapped[list[str]] = mapped_column(JSONText(), default=list)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class User(ReferenceMixin, Base):
    __tablename__ = "users"
    __audit_mask__: ClassVar[tuple[str, ...]] = ("password_hash",)

    username: Mapped[str] = mapped_column(Unicode(50), unique=True)
    display_name: Mapped[str] = mapped_column(Unicode(100))
    password_hash: Mapped[str] = mapped_column(String(200))
    permissions: Mapped[list[str]] = mapped_column(JSONText(), default=list)
    role_preset_code: Mapped[str | None] = mapped_column(String(40), default=None)
    theme: Mapped[str | None] = mapped_column(String(10), default=None)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(EventMixin, Base):
    """Who changed which reference record, when, where, old → new (SPEC §4.1)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity", "entity_id"),)

    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    action: Mapped[str] = mapped_column(String(20))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONText())
    reason: Mapped[str | None] = mapped_column(Unicode(500), default=None)


class ShiftEvent(EventMixin, Base):
    """Operator login / logout (shift sessions)."""

    __tablename__ = "shift_events"
    __table_args__ = (Index("ix_shift_events_user", "user_id", "created_at_utc"),)

    user_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    kind: Mapped[str] = mapped_column(String(10))
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
