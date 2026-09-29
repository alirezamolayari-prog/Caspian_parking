from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from caspian_parking.core.ids import is_uuid7, uuid7
from caspian_parking.data.models import AuditLog, Gate, Setting, ShiftEvent, User
from caspian_parking.data.repositories.base import StaleRecordError
from caspian_parking.data.repositories.system import AuditRepository, GateRepository, SettingsRepository
from caspian_parking.data.session import (
    AppendOnlyViolation,
    DeletionNotAllowed,
    MissingWriteContext,
    WriteContext,
    make_session_factory,
    unit_of_work,
)


@pytest.fixture
def uow(any_engine, write_ctx):
    factory = make_session_factory(any_engine)

    def _open(ctx: WriteContext | None = None):
        return unit_of_work(factory, ctx or write_ctx)

    return _open


def _gate(code: int | None = None) -> Gate:
    return Gate(code=code if code is not None else int(uuid7()[-6:], 16), name="درب آزمایشی")


def test_new_records_get_identity_and_audit(uow, write_ctx, clock):
    with uow() as session:
        gate = GateRepository(session).add(_gate())
        gate_id = gate.id
    with uow() as session:
        stored = session.get(Gate, gate_id)
        assert is_uuid7(stored.id)
        assert stored.origin_node == write_ctx.node_id
        assert stored.created_at_utc == clock.now_utc()
        assert stored.row_version == 1
        assert stored.name == "درب آزمایشی"  # Unicode round-trip on both dialects
        entries = AuditRepository(session).for_record("gates", gate_id)
        assert [e.action for e in entries] == ["create"]
        assert entries[0].changes["name"] == "درب آزمایشی"


def test_update_bumps_version_and_writes_old_new(uow, clock):
    with uow() as session:
        gate_id = GateRepository(session).add(_gate()).id
    clock.advance(minutes=5)
    with uow() as session:
        repo = GateRepository(session)
        gate = repo.get(gate_id)
        repo.update(gate, expected_version=1, reason="تغییر نام", name="درب جدید")
    with uow() as session:
        gate = session.get(Gate, gate_id)
        assert gate.row_version == 2
        assert gate.updated_at_utc == clock.now_utc()
        entries = sorted(AuditRepository(session).for_record("gates", gate_id), key=lambda e: e.created_at_utc)
        assert [e.action for e in entries] == ["create", "update"]
        assert entries[1].changes == {"name": ["درب آزمایشی", "درب جدید"]}
        assert entries[1].reason == "تغییر نام"


def test_stale_version_is_rejected(uow):
    with uow() as session:
        gate_id = GateRepository(session).add(_gate()).id
    with uow() as session, pytest.raises(StaleRecordError):
        GateRepository(session).update(session.get(Gate, gate_id), expected_version=7, name="x")


def test_concurrent_edit_detected_by_row_version(any_engine, write_ctx):
    factory = make_session_factory(any_engine)
    with unit_of_work(factory, write_ctx) as session:
        gate_id = GateRepository(session).add(_gate()).id
    first = factory()
    first.info["ctx"] = write_ctx
    second = factory()
    second.info["ctx"] = write_ctx
    try:
        a = first.get(Gate, gate_id)
        b = second.get(Gate, gate_id)
        a.name = "A"
        first.commit()
        b.name = "B"
        with pytest.raises(StaleDataError):
            second.commit()
    finally:
        first.close()
        second.rollback()
        second.close()


def test_deactivate_is_audited(uow):
    with uow() as session:
        repo = GateRepository(session)
        gate = repo.add(_gate())
        repo.deactivate(gate, reason="بسته شد")
        entries = AuditRepository(session).for_record("gates", gate.id)
        assert {e.action for e in entries} == {"create", "deactivate"}


def test_reference_records_cannot_be_deleted(uow):
    with pytest.raises(DeletionNotAllowed), uow() as session:
        gate = GateRepository(session).add(_gate())
        session.delete(gate)
        session.flush()


def test_events_cannot_be_modified_or_deleted_via_orm(uow):
    with uow() as session:
        event = ShiftEvent(user_id=uuid7(), kind="login", gate_code=1)
        session.add(event)
        session.flush()
        event_id = event.id
    with pytest.raises(AppendOnlyViolation), uow() as session:
        session.get(ShiftEvent, event_id).kind = "logout"
        session.flush()
    with pytest.raises(AppendOnlyViolation), uow() as session:
        session.delete(session.get(ShiftEvent, event_id))
        session.flush()
    with pytest.raises(AppendOnlyViolation), uow() as session:
        session.execute(update(ShiftEvent).values(kind="x"))
    with pytest.raises(AppendOnlyViolation), uow() as session:
        session.execute(delete(AuditLog))


def test_database_triggers_block_raw_sql(any_engine, uow):
    with uow() as session:
        session.add(ShiftEvent(user_id=uuid7(), kind="login", gate_code=1))
        GateRepository(session).add(_gate())  # creates an audit_log row
    for sql in ("UPDATE shift_events SET kind = 'x'", "DELETE FROM shift_events", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"), any_engine.begin() as conn:
            conn.execute(text(sql))


def test_writes_without_context_are_refused(any_engine):
    session = make_session_factory(any_engine)()
    try:
        session.add(_gate())
        with pytest.raises(MissingWriteContext):
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_unique_constraint(uow):
    with pytest.raises(IntegrityError), uow() as session:
        repo = GateRepository(session)
        repo.add(_gate(code=987654))
        repo.add(_gate(code=987654))


def test_settings_json_and_masking(uow):
    with uow() as session:
        repo = SettingsRepository(session)
        repo.set("test.value", {"a": [1, 2], "fa": "سلام"})
        repo.set("test.value", {"a": [1, 2], "fa": "سلام"})  # unchanged: no new audit
        assert repo.get("test.value") == {"a": [1, 2], "fa": "سلام"}
        assert repo.get("test.missing", 5) == 5
        setting = repo.by_key("test.value")
        assert len(AuditRepository(session).for_record("settings", setting.id)) == 1
        user = User(username=uuid7()[:8], display_name="x", password_hash="secret-hash", permissions=[])
        session.add(user)
        session.flush()
        entry = AuditRepository(session).for_record("users", user.id)[0]
        assert entry.changes["password_hash"] == "***"


def test_utc_datetimes_roundtrip_with_microseconds(uow, clock):
    moment = clock.now_utc() + timedelta(microseconds=123456)
    with uow() as session:
        event = ShiftEvent(user_id=uuid7(), kind="login", created_at_utc=moment)
        session.add(event)
        session.flush()
        event_id = event.id
    with uow() as session:
        stored = session.scalar(select(ShiftEvent).where(ShiftEvent.id == event_id))
        assert stored.created_at_utc == moment
        assert stored.created_at_utc.tzinfo is not None


def test_setting_model_value_is_json(uow):
    with uow() as session:
        session.add(Setting(key="k." + uuid7(), value=[1, "۲"]))
