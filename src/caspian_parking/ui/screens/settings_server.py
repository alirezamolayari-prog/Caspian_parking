"""Settings → Server & sync (SPEC §2.1, §7): role of this PC, central SQL Server connection, joining a gate
to the server, sync status and the update share. Role and connection changes apply after a restart."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from sqlalchemy import text

from caspian_parking.config.machine import Role
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime
from caspian_parking.services.context import SQL_PASSWORD_SECRET, AppContext, server_engine_for
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.services.sync import SyncError, join_server
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import confirm, show_toast

ODBC_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 13 for SQL Server")


class ServerTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)
        config = ctx.config

        role_card = Card(tr("server.role_card"))
        role_card.add(label(tr("server.role_hint"), "muted", wrap=True))
        self.role = QComboBox()
        for role in Role:
            self.role.addItem(tr(f"role_machine.{role.value}"), role.value)
        self.role.setCurrentIndex(max(0, self.role.findData(config.role.value)))
        role_card.add(self.role)
        layout.addWidget(role_card)

        server_card = Card(tr("server.connection"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        self.host = TextField(r"PC-SERVER\SQLEXPRESS", persian_digits=False)
        self.host.setText(config.server.host)
        self.database = TextField("parking", persian_digits=False)
        self.database.setText(config.server.database)
        self.driver = QComboBox()
        self.driver.setEditable(True)
        self.driver.addItems(ODBC_DRIVERS)
        self.driver.setCurrentText(config.server.driver)
        self.windows_auth = QCheckBox(tr("server.windows_auth"))
        self.windows_auth.setChecked(config.server.windows_auth)
        self.user = TextField(tr("server.user"), persian_digits=False)
        self.user.setText(config.server.user)
        self.password = TextField(tr("server.password"), persian_digits=False)
        self.password.setEchoMode(TextField.EchoMode.Password)
        pairs = [
            ("server.host", self.host),
            ("server.database", self.database),
            ("server.driver", self.driver),
            ("server.user", self.user),
            ("server.password", self.password),
        ]
        for index, (key, widget) in enumerate(pairs):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        server_card.body().addLayout(grid)
        server_card.add(self.windows_auth)
        buttons = QHBoxLayout()
        self.status = label("", "muted", wrap=True)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(Button(tr("server.test"), "cable", on_click=self.test_connection))
        buttons.addWidget(Button(tr("server.join"), "server", variant="primary", on_click=self.join))
        server_card.body().addLayout(buttons)
        layout.addWidget(server_card)

        update_card = Card(tr("server.update_card"))
        update_card.add(label(tr("server.update_hint"), "muted", wrap=True))
        self.update_share = TextField(r"\\PC-SERVER\ParkingUpdates", persian_digits=False)
        with ctx.read() as session:
            self.update_share.setText(str(get_setting(session, "update.share") or ""))
        update_card.add(self.update_share)
        layout.addWidget(update_card)

        save_row = QHBoxLayout()
        self.joined = label(self._joined_text(), "muted")
        save_row.addWidget(self.joined, 1)
        save_row.addWidget(Button(tr("common.save"), "check", variant="primary", on_click=self.save))
        layout.addLayout(save_row)
        layout.addStretch(1)

    def _joined_text(self) -> str:
        if not self.ctx.config.joined_at:
            return tr("server.not_joined")
        from datetime import datetime

        return tr("server.joined_at", when=fa_datetime(datetime.fromisoformat(self.ctx.config.joined_at)))

    def _apply_fields(self) -> None:
        server = self.ctx.config.server
        server.host = self.host.text().strip()
        server.database = self.database.text().strip() or "parking"
        server.driver = self.driver.currentText().strip()
        server.windows_auth = self.windows_auth.isChecked()
        server.user = self.user.text().strip()
        if self.password.text():
            self.ctx.secrets.set(SQL_PASSWORD_SECRET, self.password.text())

    def save(self) -> None:
        self._apply_fields()
        self.ctx.config.role = Role(self.role.currentData())
        self.ctx.save_config()
        with self.ctx.uow() as session:
            set_setting(session, "update.share", self.update_share.text().strip())
        show_toast(self, tr("server.saved_restart"))

    def test_connection(self) -> bool:
        self._apply_fields()
        if not self.ctx.config.server.host:
            self.status.setText(tr("server.no_host"))
            return False
        engine = server_engine_for(self.ctx.config, self.ctx.secrets)
        try:
            with engine.connect() as conn:
                version = str(conn.execute(text("SELECT @@VERSION")).scalar_one()).splitlines()[0]
        except Exception as exc:  # any driver / network error is shown to the installer
            self.status.setText(tr("server.test_failed", error=str(exc).splitlines()[0][:160]))
            return False
        finally:
            engine.dispose()
        self.status.setText(tr("server.test_ok", version=version))
        return True

    def join(self, ask: bool = True) -> bool:
        """Make this PC a gate of the server (local database set aside if unused)."""
        if ask and not confirm(self, tr("server.join"), tr("server.join_confirm")):  # pragma: no cover - dialog
            return False
        self._apply_fields()
        self.ctx.save_config()
        engine = server_engine_for(self.ctx.config, self.ctx.secrets)
        self.ctx.engine.dispose()  # release the local database file so it can be set aside (Windows locks it)
        try:
            join_server(self.ctx.data_root.root, engine, self.ctx.clock)
        except SyncError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        except Exception as exc:  # connection problems
            show_toast(self, tr("server.test_failed", error=str(exc).splitlines()[0][:160]), "error")
            return False
        finally:
            engine.dispose()
        show_toast(self, tr("server.joined_restart"))
        return True
