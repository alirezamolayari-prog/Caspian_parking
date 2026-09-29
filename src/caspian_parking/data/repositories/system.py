"""Repositories for the system tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select

from caspian_parking.data.models import AuditLog, Gate, Level, Node, RolePreset, Setting, ShiftEvent, User
from caspian_parking.data.repositories.base import EventRepository, ReferenceRepository


class GateRepository(ReferenceRepository[Gate]):
    model = Gate

    def _base_query(self) -> Select[Any]:
        return select(Gate).order_by(Gate.sort, Gate.code)

    def by_code(self, code: int) -> Gate | None:
        return self.session.scalar(select(Gate).where(Gate.code == code))


class LevelRepository(ReferenceRepository[Level]):
    model = Level

    def _base_query(self) -> Select[Any]:
        return select(Level).order_by(Level.sort, Level.code)

    def by_code(self, code: str) -> Level | None:
        return self.session.scalar(select(Level).where(Level.code == code))


class NodeRepository(ReferenceRepository[Node]):
    model = Node


class RolePresetRepository(ReferenceRepository[RolePreset]):
    model = RolePreset

    def by_code(self, code: str) -> RolePreset | None:
        return self.session.scalar(select(RolePreset).where(RolePreset.code == code))


class UserRepository(ReferenceRepository[User]):
    model = User

    def _base_query(self) -> Select[Any]:
        return select(User).order_by(User.display_name)

    def by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(User.username == username.strip().lower()))

    def search(self, text: str) -> Select[Any]:
        like = f"%{text.strip()}%"
        return self._base_query().where(or_(User.username.ilike(like), User.display_name.ilike(like)))

    def any_exists(self) -> bool:
        return bool(self.session.scalar(select(func.count()).select_from(User)))


class SettingsRepository(ReferenceRepository[Setting]):
    model = Setting

    def by_key(self, key: str) -> Setting | None:
        return self.session.scalar(select(Setting).where(Setting.key == key))

    def get(self, key: str, default: Any = None) -> Any:
        row = self.by_key(key)
        return default if row is None else row.value

    def set(self, key: str, value: Any, reason: str | None = None) -> Setting:
        row = self.by_key(key)
        if row is None:
            return self.add(Setting(key=key, value=value))
        if row.value != value:
            self.update(row, reason=reason, value=value)
        return row

    def with_prefix(self, prefix: str) -> dict[str, Any]:
        rows = self.session.scalars(select(Setting).where(Setting.key.startswith(prefix))).all()
        return {row.key: row.value for row in rows}


class AuditRepository(EventRepository[AuditLog]):
    model = AuditLog

    def filtered(
        self,
        entity: str | None = None,
        entity_id: str | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select[Any]:
        stmt = self._base_query()
        if entity:
            stmt = stmt.where(AuditLog.entity == entity)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if user_id:
            stmt = stmt.where(AuditLog.created_by == user_id)
        if since:
            stmt = stmt.where(AuditLog.created_at_utc >= since)
        if until:
            stmt = stmt.where(AuditLog.created_at_utc < until)
        return stmt

    def for_record(self, entity: str, entity_id: str) -> Sequence[AuditLog]:
        return self.session.scalars(self.filtered(entity, entity_id)).all()


class ShiftRepository(EventRepository[ShiftEvent]):
    model = ShiftEvent

    def last_for_user(self, user_id: str) -> ShiftEvent | None:
        return self.session.scalar(
            select(ShiftEvent).where(ShiftEvent.user_id == user_id).order_by(ShiftEvent.created_at_utc.desc())
        )
