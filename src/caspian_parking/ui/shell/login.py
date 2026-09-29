"""Login, first-admin creation and password change dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout, QWidget

from caspian_parking.config.defaults import product_name
from caspian_parking.data.models import User
from caspian_parking.i18n import tr
from caspian_parking.services import auth
from caspian_parking.services.auth import AuthError
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting
from caspian_parking.ui.theme.icons import pixmap
from caspian_parking.ui.theme.manager import ThemeManager, set_dark_title_bar
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label


class _CenteredDialog(QDialog):
    """Full-window dialog with a centered card (login-style screens)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XXL)
        outer.addStretch(1)
        self.card = Card(raised=True)
        self.card.setMaximumWidth(440)
        self.card.body().setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XXL)
        self.card.body().setSpacing(Space.M)
        outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        self.glyph = label()
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.add(self.glyph)
        self.error = label("", "danger", wrap=True)
        self.error.setVisible(False)
        ThemeManager.instance().changed.connect(self._refresh_glyph)
        self._refresh_glyph()

    def _refresh_glyph(self, *_args: object) -> None:
        self.glyph.setPixmap(pixmap("circle-parking", "accent_text", 56))

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def showEvent(self, event: object) -> None:
        set_dark_title_bar(self, ThemeManager.instance().palette.is_dark)
        super().showEvent(event)  # type: ignore[arg-type]


def _password_field(placeholder: str) -> TextField:
    field = TextField(placeholder, persian_digits=False)
    field.setEchoMode(QLineEdit.EchoMode.Password)
    return field


class LoginDialog(_CenteredDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(tr("login.window_title"), parent)
        self.ctx = ctx
        self.user: User | None = None
        with ctx.read() as session:
            mall = get_setting(session, "site.mall_name") or product_name()
        title = label(mall, "h2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.add(title)
        subtitle = label(tr("login.subtitle"), "muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.add(subtitle)
        self.username = TextField(tr("login.username"), persian_digits=False)
        self.password = _password_field(tr("login.password"))
        self.card.add(self.username)
        self.card.add(self.password)
        self.card.add(self.error)
        self.submit = Button(tr("login.submit"), "log-in", variant="primary", size="lg", on_click=self.try_login)
        self.submit.setDefault(True)
        self.card.add(self.submit)
        if ctx.training:
            badge = label(tr("training.banner"), "accent")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card.add(badge)
        self.password.returnPressed.connect(self.try_login)
        self.username.returnPressed.connect(self.password.setFocus)
        self.username.setFocus()

    def try_login(self) -> bool:
        with self.ctx.uow() as session:
            user = auth.authenticate(session, self.username.value(), self.password.text())
        if user is None:
            self.show_error(tr("login.failed"))
            self.password.selectAll()
            self.password.setFocus()
            return False
        self.user = user
        self.accept()
        return True


class CreateAdminDialog(_CenteredDialog):
    """Shown when the database has no users yet (first start)."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(tr("admin.create_title"), parent)
        self.ctx = ctx
        self.user: User | None = None
        self.card.add(label(tr("admin.create_title"), "h2"))
        self.card.add(label(tr("admin.create_hint"), "muted", wrap=True))
        self.display_name = TextField(tr("user.display_name"))
        self.username = TextField(tr("login.username"), persian_digits=False)
        self.password = _password_field(tr("login.password"))
        self.repeat = _password_field(tr("user.password_repeat"))
        for widget in (self.display_name, self.username, self.password, self.repeat, self.error):
            self.card.add(widget)
        self.submit = Button(
            tr("admin.create_submit"), "user-plus", variant="primary", size="lg", on_click=self.create_admin
        )
        self.card.add(self.submit)

    def create_admin(self) -> bool:
        if self.password.text() != self.repeat.text():
            self.show_error(tr("user.password_mismatch"))
            return False
        try:
            with self.ctx.uow() as session:
                self.user = auth.create_user(
                    session, self.username.value(), self.display_name.value(), self.password.text(), preset="admin"
                )
        except AuthError as exc:
            self.show_error(tr(str(exc)))
            return False
        self.accept()
        return True


class ChangePasswordDialog(_CenteredDialog):
    def __init__(self, ctx: AppContext, user_id: str, forced: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(tr("password.change_title"), parent)
        self.ctx = ctx
        self.user_id = user_id
        self.card.add(label(tr("password.change_title"), "h2"))
        if forced:
            self.card.add(label(tr("password.change_forced"), "muted", wrap=True))
        self.password = _password_field(tr("password.new"))
        self.repeat = _password_field(tr("user.password_repeat"))
        for widget in (self.password, self.repeat, self.error):
            self.card.add(widget)
        self.card.add(Button(tr("common.save"), "check", variant="primary", size="lg", on_click=self.save))

    def save(self) -> bool:
        if self.password.text() != self.repeat.text():
            self.show_error(tr("user.password_mismatch"))
            return False
        try:
            with self.ctx.uow() as session:
                user = session.get(User, self.user_id)
                assert user is not None
                auth.change_password(session, user, self.password.text())
        except AuthError as exc:
            self.show_error(tr(str(exc)))
            return False
        self.accept()
        return True
