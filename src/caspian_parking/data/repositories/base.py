"""Generic repositories.

``ReferenceRepository`` edits versioned reference data (audit is written automatically by the
session hooks). ``EventRepository`` has no update or delete methods at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from caspian_parking.data.base import EntityMixin, EventMixin, ReferenceMixin
from caspian_parking.data.session import audit_reason


class StaleRecordError(RuntimeError):
    """The record was changed by someone else since it was loaded."""


class _Repository[T: EntityMixin]:
    model: ClassVar[type[Any]]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, record_id: str) -> T | None:
        return self.session.get(self.model, record_id)

    def _base_query(self) -> Select[Any]:
        return select(self.model)

    def page(self, offset: int = 0, limit: int = 100, query: Select[Any] | None = None) -> Sequence[T]:
        stmt = query if query is not None else self._base_query()
        return self.session.scalars(stmt.offset(offset).limit(limit)).all()

    def count(self, query: Select[Any] | None = None) -> int:
        stmt = query if query is not None else self._base_query()
        return int(self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


class ReferenceRepository[T: ReferenceMixin](_Repository[T]):
    def _base_query(self) -> Select[Any]:
        return select(self.model).order_by(self.model.created_at_utc)

    def active(self) -> Sequence[T]:
        return self.session.scalars(self._base_query().where(self.model.is_active.is_(True))).all()

    def add(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj

    def update(self, obj: T, expected_version: int | None = None, reason: str | None = None, **changes: Any) -> T:
        if expected_version is not None and obj.row_version != expected_version:
            raise StaleRecordError(f"{type(obj).__name__} {obj.id} changed (v{obj.row_version})")
        for key, value in changes.items():
            if not hasattr(obj, key):
                raise AttributeError(f"{type(obj).__name__} has no field {key}")
            setattr(obj, key, value)
        if reason:
            with audit_reason(self.session, reason):
                pass
        else:
            self.session.flush()
        return obj

    def deactivate(self, obj: T, reason: str) -> T:
        return self.update(obj, reason=reason, is_active=False)

    def reactivate(self, obj: T, reason: str) -> T:
        return self.update(obj, reason=reason, is_active=True)


class EventRepository[T: EventMixin](_Repository[T]):
    def _base_query(self) -> Select[Any]:
        return select(self.model).order_by(self.model.created_at_utc.desc())

    def append(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj
