"""Application bootstrap: theme, database, login loop and the main window."""

from __future__ import annotations

import argparse
import logging
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from caspian_parking.config.defaults import product_name
from caspian_parking.data.models import User
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.i18n import tr
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.context import AppContext, open_context
from caspian_parking.services.heartbeat import detect_outage
from caspian_parking.services.scheduler import AppScheduler
from caspian_parking.ui.shell.main_window import MainWindow
from caspian_parking.ui.theme.manager import ThemeManager, load_fonts

log = logging.getLogger(__name__)

SMOKE_EXIT_MS = 300
SMOKE_USER = "smoke-admin"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="parking")
    parser.add_argument("--smoke", action="store_true", help="open the main window, then exit with code 0")
    parser.add_argument("--data-root", type=Path, default=None, help="override the data root folder")
    parser.add_argument("--training", action="store_true", help="start in training mode (sandbox database)")
    parser.add_argument("--server", action="store_true", help="run the server role without a window")
    parser.add_argument("--service", metavar="COMMAND", help="Windows service: install | start | stop | remove")
    return parser.parse_args(argv)


def create_application() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setApplicationName(product_name())
    QLocale.setDefault(QLocale(QLocale.Language.Persian, QLocale.Country.Iran))  # Persian digits in inputs
    load_fonts()
    return app


def build_main_window(ctx: AppContext) -> MainWindow:
    """Create the main window for the logged-in user (theme follows the user's choice)."""
    mode = ctx.user.theme if ctx.user and ctx.user.theme else ctx.config.theme_default
    ThemeManager.instance().apply(mode)
    return MainWindow(ctx)


class SessionController(QObject):
    """First admin → login → (forced password change) → main window → logout → login …"""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.window: MainWindow | None = None

    def start(self) -> bool:
        from caspian_parking.ui.shell.login import CreateAdminDialog

        with self.ctx.read() as session:
            has_users = UserRepository(session).any_exists()
        if not has_users and CreateAdminDialog(self.ctx).exec() != QDialog.DialogCode.Accepted:
            return False
        return self.login()

    def login(self) -> bool:
        from caspian_parking.ui.shell.login import ChangePasswordDialog, LoginDialog

        dialog = LoginDialog(self.ctx)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.user is None:
            return False
        user = dialog.user
        if user.must_change_password:
            if ChangePasswordDialog(self.ctx, user.id, forced=True).exec() != QDialog.DialogCode.Accepted:
                return False
            with self.ctx.read() as session:
                refreshed = session.get(User, user.id)
                assert refreshed is not None
                user = refreshed
        self.ctx.user = CurrentUser.from_user(user)
        with self.ctx.uow() as session:
            auth.start_shift(session, user.id, self.ctx.config.gate_code)
        self.window = build_main_window(self.ctx)
        self.window.logout_requested.connect(self.logout)
        self.window.showMaximized()
        from caspian_parking.ui.shell.morning import show_morning_report

        show_morning_report(self.window, self.ctx)
        return True

    def logout(self) -> None:
        if self.ctx.user is not None:
            with self.ctx.uow() as session:
                auth.end_shift(session, self.ctx.user.id, self.ctx.config.gate_code)
        self.ctx.user = None
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.window = None
        if not self.login():
            app = QApplication.instance()
            if app is not None:
                app.quit()


def smoke_login(ctx: AppContext) -> None:
    """Smoke mode: log in as a throw-away admin (created with a random password)."""
    with ctx.uow() as session:
        user = UserRepository(session).by_username(SMOKE_USER)
        if user is None:
            user = auth.create_user(session, SMOKE_USER, "Smoke", secrets.token_urlsafe(16), preset="admin")
        ctx.user = CurrentUser.from_user(user)


def run_smoke(app: QApplication, ctx: AppContext, started: float) -> int:
    smoke_login(ctx)
    window = build_main_window(ctx)
    window.show()
    app.processEvents()
    ready = time.perf_counter() - started
    log.info("smoke: main window ready in %.2f s", ready)
    print(f"main window ready in {ready:.2f} s")
    QTimer.singleShot(SMOKE_EXIT_MS, app.quit)
    code = app.exec()
    window.close()
    return code


def shutdown_tasks(ctx: AppContext) -> None:
    """On close: backup (when enabled) and mark a clean shutdown for the outage log."""
    from caspian_parking.services.backup import BackupError, create_backup
    from caspian_parking.services.heartbeat import mark_clean_shutdown
    from caspian_parking.services.settings import get_setting

    try:
        with ctx.read() as session:
            on_close = bool(get_setting(session, "backup.on_close"))
        if on_close:
            create_backup(ctx, reason="on-close")
    except (BackupError, OSError):
        log.exception("backup on close failed")
    mark_clean_shutdown(ctx)


def update_at_start(ctx: AppContext) -> bool:
    """Gates install a newer version from the server share before anyone logs in (SPEC §7)."""
    from caspian_parking.config.machine import Role
    from caspian_parking.services.settings import get_setting
    from caspian_parking.services.updater import apply_update, check_for_update

    if ctx.config.role is not Role.GATE:
        return False
    with ctx.read() as session:
        share = str(get_setting(session, "update.share") or "")
    info = check_for_update(share) if share else None
    if info is None:
        return False
    apply_update(info)
    return True


def first_run(ctx: AppContext) -> AppContext | None:
    """Fresh installation: run the setup wizard and reopen the database where the user chose."""
    from caspian_parking.services.setup import SetupError, apply_setup, needs_wizard
    from caspian_parking.ui.shell.wizard import FirstRunWizard

    if not needs_wizard(ctx):
        return ctx
    error = ""
    while True:
        wizard = FirstRunWizard(ctx)
        wizard.error.setText(error)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            ctx.close()
            return None
        try:
            return apply_setup(ctx, wizard.choices())
        except Exception as exc:  # e.g. the server is not reachable: show it and let the user correct it
            log.warning("setup failed: %s", exc)
            error = tr(str(exc)) if isinstance(exc, SetupError) else str(exc).splitlines()[0][:200]
            ctx = open_context(ctx.data_root.root)


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv or [])
    if args.service:  # pragma: no cover - Windows service control
        from caspian_parking.services.winservice import handle_command_line

        return handle_command_line([args.service])
    if args.server:  # pragma: no cover - long-running headless host
        from caspian_parking.services.server_host import run_server

        return run_server(args.data_root)
    app = create_application()
    temp_root: Path | None = None
    root = args.data_root
    if args.smoke and root is None:
        temp_root = Path(tempfile.mkdtemp(prefix="parking-smoke-"))
        root = temp_root
    ctx = open_context(root, training=True if args.training else None)
    stoppers: list[Callable[[], None]] = []
    try:
        ThemeManager.instance().apply(ctx.config.theme_default)
        if args.smoke:
            return run_smoke(app, ctx, started)
        opened = first_run(ctx)
        if opened is None:
            return 0
        ctx = opened
        if update_at_start(ctx):
            return 0
        detect_outage(ctx)
        scheduler = AppScheduler(ctx)
        scheduler.start()
        stoppers.append(scheduler.stop)
        stoppers.append(lambda: shutdown_tasks(ctx))
        controller = SessionController(ctx)
        if not controller.start():
            return 0
        return app.exec()
    finally:
        for stop in stoppers:
            stop()
        ctx.close()
        if temp_root is not None:
            logging.shutdown()
            shutil.rmtree(temp_root, ignore_errors=True)
