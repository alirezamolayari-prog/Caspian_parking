from __future__ import annotations

from PySide6.QtCore import Qt
from sqlalchemy import select

from caspian_parking.app import SessionController, build_main_window, smoke_login
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import AuditLog, Level, ShiftEvent, User
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.services.auth import AuthError, CurrentUser
from caspian_parking.ui.shell.login import ChangePasswordDialog, CreateAdminDialog, LoginDialog
from caspian_parking.ui.theme.manager import ThemeManager
from tests.ui.conftest import make_user


def test_create_admin_then_login(qtbot, app_ctx, themed):
    dialog = CreateAdminDialog(app_ctx)
    qtbot.addWidget(dialog)
    dialog.display_name.setText("مدیر")
    dialog.username.setText("boss")
    dialog.password.setText("secret1")
    dialog.repeat.setText("different")
    assert not dialog.create_admin()
    assert dialog.error.text()
    dialog.repeat.setText("secret1")
    assert dialog.create_admin()
    assert set(dialog.user.permissions) >= {Permission.MANAGE_USERS}

    login = LoginDialog(app_ctx)
    qtbot.addWidget(login)
    login.username.setText("boss")
    login.password.setText("bad")
    assert not login.try_login()
    assert login.error.isVisibleTo(login)
    login.password.setText("secret1")
    assert login.try_login()
    assert login.user.username == "boss"


def test_forced_password_change(qtbot, app_ctx, themed):
    user = make_user(app_ctx, "newbie", preset="operator")
    dialog = ChangePasswordDialog(app_ctx, user.id, forced=True)
    qtbot.addWidget(dialog)
    dialog.password.setText("12")
    dialog.repeat.setText("12")
    assert not dialog.save()
    dialog.password.setText("fresh-pass")
    dialog.repeat.setText("fresh-pass")
    assert dialog.save()


def test_operator_sees_only_permitted_screens(qtbot, operator_ctx):
    window = build_main_window(operator_ctx)
    qtbot.addWidget(window)
    keys = {spec.key for spec in window.screens}
    assert "home" in keys and "settings" in keys
    assert "users" not in keys and "audit" not in keys
    assert window.show_screen("users") is None
    settings = window.show_screen("settings")
    assert settings.tabs.count() == 1  # site tab needs CHANGE_SETTINGS


def test_admin_navigation_palette_and_sidebar(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    window.show()
    assert {"home", "users", "audit", "settings"} <= {s.key for s in window.screens}
    palette = window.open_palette()
    palette.search.setText("کاربر")  # QTest cannot type Persian keys offscreen
    assert palette.item_count() >= 1
    palette.activate_current()
    assert window.current_key() == "users"
    window.sidebar.toggle()
    assert window.sidebar.is_collapsed()
    assert window.sidebar.buttons["home"].text() == ""
    window.sidebar.toggle()
    assert window.sidebar.buttons["home"].text()
    assert "Smoke" not in window.topbar.user_button.text()


def test_theme_toggle_is_remembered_per_user(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    before = ThemeManager.instance().palette.name
    window.toggle_theme()
    after = ThemeManager.instance().palette.name
    assert before != after
    with admin_ctx.read() as session:
        assert session.get(User, admin_ctx.user.id).theme == after


def test_users_screen_creates_user_with_matrix(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("users")
    screen.new_user()
    screen.display_name.setText("سرپرست شب")
    screen.username.setText("night")
    screen.password.setText("secret1")
    screen.preset.setCurrentIndex(screen.preset.findData("supervisor"))
    screen.checks[Permission.CLOSE_FISCAL_YEAR].setChecked(True)
    assert screen.preset.currentData() == ""  # switched to custom
    assert screen.save()
    with admin_ctx.read() as session:
        user = UserRepository(session).by_username("night")
        assert Permission.CLOSE_FISCAL_YEAR in user.permissions
        assert Permission.MANAGE_BLOCKLIST in user.permissions
        assert user.must_change_password
    assert screen.model.loaded_count() == 2


def test_users_screen_prevents_self_lockout(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("users")
    with admin_ctx.read() as session:
        me = session.get(User, admin_ctx.user.id)
    screen._select(me)
    screen.checks[Permission.MANAGE_USERS].setChecked(False)
    assert not screen.save()
    with admin_ctx.read() as session:
        assert Permission.MANAGE_USERS in session.get(User, admin_ctx.user.id).permissions
    assert isinstance(AuthError("users.self_lockout"), RuntimeError)


def test_settings_site_save_is_audited(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("settings")
    level_id, (_name, capacity, _parking, _open) = next(iter(screen.level_fields.items()))
    capacity.setValue(250)
    screen.save_site()
    with admin_ctx.read() as session:
        assert session.get(Level, level_id).capacity == 250
        entries = session.scalars(select(AuditLog).where(AuditLog.entity_id == level_id)).all()
        assert any(e.action == "update" and e.changes.get("capacity") == [202, 250] for e in entries)
    audit = window.show_screen("audit")
    assert audit.model.loaded_count() > 0
    audit.table.selectRow(0)
    audit._show_detail(audit.table.selected_object())
    assert audit.detail.toPlainText()


def test_shortcuts_dialog_and_user_menu(qtbot, admin_ctx):
    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    window.show_shortcuts()
    actions = [a.text() for a in window.topbar.user_menu.actions() if a.text()]
    assert len(actions) == 3


def test_session_controller_logout_writes_shift_events(qtbot, app_ctx, themed, monkeypatch):
    make_user(app_ctx, "gateop", preset="operator")

    def fake_exec(self):
        self.username.setText("gateop")
        self.password.setText("secret1")
        return 1 if self.try_login() else 0

    monkeypatch.setattr(LoginDialog, "exec", fake_exec)
    controller = SessionController(app_ctx)
    assert controller.start()
    qtbot.addWidget(controller.window)
    assert app_ctx.user.username == "gateop"
    monkeypatch.setattr(LoginDialog, "exec", lambda self: 0)
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.quit", lambda *_a: None)
    controller.logout()
    assert app_ctx.user is None
    with app_ctx.read() as session:
        kinds = [e.kind for e in session.scalars(select(ShiftEvent).order_by(ShiftEvent.id))]
    assert kinds == ["login", "logout"]


def test_smoke_login_creates_admin(app_ctx, themed):
    smoke_login(app_ctx)
    assert isinstance(app_ctx.user, CurrentUser)
    assert app_ctx.user.can(Permission.MANAGE_USERS)
    assert Qt.LayoutDirection.RightToLeft is not None


def test_admin_can_create_custom_role_preset(qtbot, admin_ctx):
    from caspian_parking.data.repositories.system import RolePresetRepository

    window = build_main_window(admin_ctx)
    qtbot.addWidget(window)
    screen = window.show_screen("users")
    for box in screen.checks.values():
        box.setChecked(False)
    screen.checks[Permission.OPERATE_GATE].setChecked(True)
    screen.checks[Permission.GRANT_GUEST].setChecked(True)
    assert screen.save_as_preset("") is None
    preset = screen.save_as_preset("اپراتور شب")
    assert preset is not None
    assert screen.preset.currentData() == preset.code
    with admin_ctx.read() as session:
        stored = RolePresetRepository(session).by_code(preset.code)
        assert sorted(stored.permissions) == sorted([Permission.OPERATE_GATE, Permission.GRANT_GUEST])
    # choosing the preset later fills the matrix
    screen.preset.setCurrentIndex(screen.preset.findData("admin"))
    assert screen.checks[Permission.MANAGE_USERS].isChecked()
    screen.preset.setCurrentIndex(screen.preset.findData(preset.code))
    assert not screen.checks[Permission.MANAGE_USERS].isChecked()
    assert screen.checks[Permission.GRANT_GUEST].isChecked()
