"""Unit of work with the write rules of SPEC §2.2 enforced for every session.

* every new record gets ``origin_node`` / ``created_at_utc`` / ``created_by`` from the WriteContext
* append-only (event) records can never be updated or deleted
* reference records are never deleted (deactivate instead) and every change writes ``audit_log``
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Delete, Update, event
from sqlalchemy.orm import InstanceState, ORMExecuteState, Session, sessionmaker
from sqlalchemy.orm.attributes import instance_state

from caspian_parking.core.clock import SYSTEM_CLOCK, Clock
from caspian_parking.core.ids import uuid7
from caspian_parking.data.base import Base, EntityMixin, ReferenceMixin, is_append_only
from caspian_parking.data.models import AuditLog

_SKIP_AUDIT_FIELDS = frozenset({"row_version", "updated_at_utc", "updated_by"})
MASKED = "***"


class AppendOnlyViolation(RuntimeError):
    """Raised when code tries to modify or delete an append-only record."""


class DeletionNotAllowed(RuntimeError):
    """Raised when code tries to delete a record that must be kept (deactivate instead)."""


class MissingWriteContext(RuntimeError):
    """Raised when a session writes data without a WriteContext."""


@dataclass
class WriteContext:
    node_id: str
    user_id: str | None = None
    clock: Clock = SYSTEM_CLOCK
    reason: str | None = None


def append_only_tables() -> frozenset[str]:
    return frozenset(
        str(getattr(mapper.local_table, "name", ""))
        for mapper in Base.registry.mappers
        if is_append_only(mapper.class_)
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _snapshot(obj: EntityMixin) -> dict[str, Any]:
    mask = getattr(type(obj), "__audit_mask__", ())
    state: InstanceState[Any] = instance_state(obj)
    data: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in _SKIP_AUDIT_FIELDS:
            continue
        value = getattr(obj, key)
        data[key] = MASKED if key in mask and value is not None else _jsonable(value)
    return data


def _changes(obj: EntityMixin) -> dict[str, list[Any]]:
    mask = getattr(type(obj), "__audit_mask__", ())
    state: InstanceState[Any] = instance_state(obj)
    changes: dict[str, list[Any]] = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in _SKIP_AUDIT_FIELDS:
            continue
        history = state.attrs[key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old == new:
            continue
        if key in mask:
            changes[key] = [MASKED, MASKED]
        else:
            changes[key] = [_jsonable(old), _jsonable(new)]
    return changes


def _audit(session: Session, ctx: WriteContext, obj: EntityMixin, action: str, changes: dict[str, Any]) -> None:
    session.add(
        AuditLog(
            id=uuid7(),
            origin_node=ctx.node_id,
            created_at_utc=ctx.clock.now_utc(),
            created_by=ctx.user_id,
            entity=type(obj).__table__.name,  # type: ignore[attr-defined]
            entity_id=obj.id,
            action=action,
            changes=changes,
            reason=ctx.reason,
        )
    )


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    new = [o for o in session.new if isinstance(o, EntityMixin)]
    dirty = [o for o in session.dirty if isinstance(o, EntityMixin) and session.is_modified(o)]
    deleted = [o for o in session.deleted if isinstance(o, EntityMixin)]
    if not (new or dirty or deleted):
        return
    ctx = session.info.get("ctx")
    if not isinstance(ctx, WriteContext):
        raise MissingWriteContext("writes need a WriteContext (use unit_of_work)")
    now = ctx.clock.now_utc()

    for obj in deleted:
        if is_append_only(obj):
            raise AppendOnlyViolation(f"cannot delete append-only record {type(obj).__name__}")
        if not getattr(type(obj), "__deletable__", False):
            raise DeletionNotAllowed(f"{type(obj).__name__} records are never deleted; deactivate instead")

    for obj in dirty:
        if is_append_only(obj):
            raise AppendOnlyViolation(f"cannot modify append-only record {type(obj).__name__}")
        if isinstance(obj, ReferenceMixin):
            changes = _changes(obj)
            if not changes:
                continue
            obj.updated_at_utc = now
            obj.updated_by = ctx.user_id
            if getattr(type(obj), "__audited__", False):
                action = "update"
                if "is_active" in changes:
                    action = "reactivate" if changes["is_active"][1] else "deactivate"
                _audit(session, ctx, obj, action, changes)

    for obj in new:
        if isinstance(obj, AuditLog):
            continue
        if obj.id is None:
            obj.id = uuid7()
        if obj.origin_node is None:
            obj.origin_node = ctx.node_id
        if obj.created_at_utc is None:
            obj.created_at_utc = now
        if obj.created_by is None:
            obj.created_by = ctx.user_id
        if isinstance(obj, ReferenceMixin):
            if obj.updated_at_utc is None:
                obj.updated_at_utc = now
            if obj.updated_by is None:
                obj.updated_by = ctx.user_id
            if obj.is_active is None:
                obj.is_active = True
            if getattr(type(obj), "__audited__", False):
                _audit(session, ctx, obj, "create", _snapshot(obj))


@event.listens_for(Session, "do_orm_execute")
def _guard_bulk_statements(state: ORMExecuteState) -> None:
    statement = state.statement
    if isinstance(statement, Update | Delete):
        table = getattr(statement, "table", None)
        name = getattr(table, "name", None)
        if name in append_only_tables():
            raise AppendOnlyViolation(f"bulk UPDATE/DELETE on append-only table {name}")


def make_session_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


@contextmanager
def unit_of_work(factory: sessionmaker[Session], ctx: WriteContext) -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    session = factory()
    session.info["ctx"] = ctx
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def read_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@contextmanager
def audit_reason(session: Session, reason: str) -> Iterator[None]:
    """Attach a reason to the audit entries written inside the block."""
    ctx = session.info["ctx"]
    previous = ctx.reason
    ctx.reason = reason
    try:
        yield
        session.flush()
    finally:
        ctx.reason = previous
