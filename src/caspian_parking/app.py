"""Application bootstrap: theme, database, login loop and the main window."""

from __future__ import annotations

import argparse
import logging
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from caspian_parking.config.defaults import product_name
from caspian_parking.data.models import User
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.context import AppContext, open_context
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


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv or [])
    app = create_application()
    temp_root: Path | None = None
    root = args.data_root
    if args.smoke and root is None:
        temp_root = Path(tempfile.mkdtemp(prefix="parking-smoke-"))
        root = temp_root
    ctx = open_context(root, training=True if args.training else None)
    try:
        ThemeManager.instance().apply(ctx.config.theme_default)
        if args.smoke:
            return run_smoke(app, ctx, started)
        controller = SessionController(ctx)
        if not controller.start():
            return 0
        return app.exec()
    finally:
        ctx.close()
        if temp_root is not None:
            logging.shutdown()
            shutil.rmtree(temp_root, ignore_errors=True)
