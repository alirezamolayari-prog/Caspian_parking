from __future__ import annotations

import pytest
from sqlalchemy import select

from caspian_parking.config.machine import load_machine_config
from caspian_parking.core.permissions import (
    ALL_PERMISSIONS,
    BUILTIN_PRESETS,
    OPERATOR_PERMISSIONS,
    Permission,
    normalize_permissions,
)
from caspian_parking.data.models import Gate, Level, Node, RolePreset, ShiftEvent
from caspian_parking.data.repositories.system import AuditRepository, UserRepository
from caspian_parking.services import auth
from caspian_parking.services.auth import AuthError, CurrentUser
from caspian_parking.services.context import open_context
from caspian_parking.services.settings import get_setting, set_setting


def test_spec_permissions_are_all_present():
    spec_minimum = {
        "tariffs.change",
        "fines.adjust",
        "amounts.adjust",
        "transactions.cancel",
        "guest.grant",
        "subscription.negative",
        "blocklist.manage",
        "subscribers.manage",
        "shops.manage",
        "ads.manage",
        "reports.financial",
        "reports.export",
        "users.manage",
        "backup.manage",
        "fiscal.close",
        "settings.change",
        "hardware.settings",
    }
    assert spec_minimum <= ALL_PERMISSIONS
    assert BUILTIN_PRESETS["admin"] == ALL_PERMISSIONS
    assert OPERATOR_PERMISSIONS < BUILTIN_PRESETS["supervisor"] < ALL_PERMISSIONS
    assert normalize_permissions(["users.manage", "bogus", "users.manage"]) == ["users.manage"]
    assert normalize_permissions("users.manage") == []


def test_password_hashing():
    encoded = auth.hash_password("رمز-قوی-۱۲۳")
    assert encoded.startswith("scrypt$")
    assert auth.verify_password("رمز-قوی-۱۲۳", encoded)
    assert not auth.verify_password("wrong", encoded)
    assert not auth.verify_password("x", "garbage")
    assert auth.hash_password("same") != auth.hash_password("same")  # salted


def test_open_context_seeds_reference_data(app_ctx):
    with app_ctx.read() as session:
        assert {g.code for g in session.scalars(select(Gate))} == {1, 2}
        levels = {lv.code: lv for lv in session.scalars(select(Level))}
        assert levels["P1"].capacity == 202
        assert not levels["P4"].is_parking
        assert {p.code for p in session.scalars(select(RolePreset))} == {"operator", "supervisor", "admin"}
        assert session.get(Node, app_ctx.node_id) is not None
        assert get_setting(session, "site.mall_name")
    assert app_ctx.data_root.local_db.is_file()


def test_open_context_is_idempotent(tmp_path, clock):
    first = open_context(tmp_path / "d", clock=clock)
    node_id = first.node_id
    first.close()
    second = open_context(tmp_path / "d", clock=clock)
    try:
        assert second.node_id == node_id
        with second.read() as session:
            assert len(session.scalars(select(Gate)).all()) == 2
    finally:
        second.close()


def test_training_mode_uses_separate_database(tmp_path, clock):
    ctx = open_context(tmp_path / "d", clock=clock, training=True)
    try:
        assert ctx.training
        assert ctx.data_root.training_db.is_file()
        assert not ctx.data_root.local_db.exists()
    finally:
        ctx.close()
    assert load_machine_config(tmp_path / "d" / "config").training_mode is False


def test_user_lifecycle_and_shifts(app_ctx):
    with app_ctx.uow() as session:
        user = auth.create_user(session, "Ali", "علی رضایی", "secret1", preset="operator")
        assert user.username == "ali"
        assert set(user.permissions) == OPERATOR_PERMISSIONS
        with pytest.raises(AuthError, match="taken"):
            auth.create_user(session, "ALI", "x", "secret1")
        with pytest.raises(AuthError, match="too_short"):
            auth.create_user(session, "bob", "x", "123")
        with pytest.raises(AuthError, match="username_required"):
            auth.create_user(session, "  ", "x", "secret1")

    with app_ctx.uow() as session:
        assert auth.authenticate(session, "ali", "wrong") is None
        assert auth.authenticate(session, "nobody", "secret1") is None
        found = auth.authenticate(session, " ALI ", "secret1")
        assert found is not None
        current = CurrentUser.from_user(found)
        assert current.can(Permission.OPERATE_GATE)
        assert not current.can(Permission.MANAGE_USERS)
        auth.start_shift(session, found.id, gate_code=1)
        auth.end_shift(session, found.id, gate_code=1)
        auth.change_password(session, found, "another1")

    with app_ctx.uow() as session:
        user = UserRepository(session).by_username("ali")
        assert auth.authenticate(session, "ali", "another1") is not None
        kinds = [e.kind for e in session.scalars(select(ShiftEvent).order_by(ShiftEvent.id))]
        assert kinds == ["login", "logout"]
        actions = [e.action for e in AuditRepository(session).for_record("users", user.id)]
        assert sorted(actions) == ["create", "update"]
        UserRepository(session).deactivate(user, reason="left")
    with app_ctx.uow() as session:
        assert auth.authenticate(session, "ali", "another1") is None


def test_settings_service(app_ctx):
    with app_ctx.uow() as session:
        assert get_setting(session, "gate.debounce_seconds") == 3
        set_setting(session, "gate.debounce_seconds", 5, reason="test")
    with app_ctx.read() as session:
        assert get_setting(session, "gate.debounce_seconds") == 5
        with pytest.raises(KeyError):
            get_setting(session, "nope")
