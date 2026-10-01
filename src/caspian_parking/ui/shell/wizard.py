"""First-run wizard (SPEC §7): theme & data folder → role & gate → central server (test) → devices →
first administrator → done. ``services/setup.py`` applies the choices."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import text

from caspian_parking.config.machine import Role
from caspian_parking.config.secrets import SecretStore
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.devices.printer import installed_printers
from caspian_parking.i18n import tr
from caspian_parking.services.context import AppContext
from caspian_parking.services.setup import SetupChoices, SetupError, validate
from caspian_parking.ui.screens.settings_devices import PRINTER_BACKENDS, SCANNER_MODES
from caspian_parking.ui.screens.settings_server import ODBC_DRIVERS
from caspian_parking.ui.theme.manager import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, ThemeManager
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, TextField, label
from caspian_parking.ui.widgets.feedback import ModalDialog

PAGES = ("welcome", "role", "server", "devices", "admin", "done")


class FirstRunWizard(ModalDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent, tr("wizard.title"), width=760)
        self.ctx = ctx
        with ctx.read() as session:
            self.needs_admin = not UserRepository(session).any_exists()
        self.step_label = label("", "caption")
        self.content.addWidget(self.step_label)
        self.stack = QStackedWidget()
        self.content.addWidget(self.stack, 1)
        self.pages = {
            "welcome": self._welcome(),
            "role": self._role(),
            "server": self._server(),
            "devices": self._devices(),
            "admin": self._admin(),
            "done": self._done(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.error = label("", "danger", wrap=True)
        self.content.addWidget(self.error)
        self.back_button = self.add_button(tr("wizard.back"), role="none")
        self.back_button.clicked.connect(self.back)
        self.next_button = self.add_button(tr("wizard.next"), variant="primary", role="none")
        self.next_button.clicked.connect(self.next)
        self.current = "welcome"
        self.show_page("welcome")

    # ---------------------------------------------------------------- pages
    def _page(self, title: str, hint: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.M)
        layout.addWidget(label(title, "h3"))
        if hint:
            layout.addWidget(label(hint, "muted", wrap=True))
        return page, layout

    def _welcome(self) -> QWidget:
        page, layout = self._page(tr("wizard.welcome_title"), tr("wizard.welcome_hint"))
        self.theme_group = QButtonGroup(self)
        row = QHBoxLayout()
        self.theme_buttons: dict[str, QRadioButton] = {}
        for mode in (THEME_DARK, THEME_LIGHT, THEME_SYSTEM):
            button = QRadioButton(tr(f"theme.{mode}"))
            button.setChecked(mode == THEME_DARK)
            button.toggled.connect(lambda on, m=mode: ThemeManager.instance().apply(m) if on else None)
            self.theme_group.addButton(button)
            self.theme_buttons[mode] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(label(tr("wizard.data_root"), "caption"))
        folder = QHBoxLayout()
        self.data_root = TextField("", persian_digits=False)
        self.data_root.setText(str(self.ctx.data_root.root))
        folder.addWidget(self.data_root, 1)
        folder.addWidget(Button(tr("wizard.browse"), "folder-open", on_click=self._browse))
        layout.addLayout(folder)
        layout.addWidget(label(tr("wizard.data_root_hint"), "muted", wrap=True))
        layout.addStretch(1)
        return page

    def _browse(self) -> None:  # pragma: no cover - native dialog
        chosen = QFileDialog.getExistingDirectory(self, tr("wizard.data_root"), self.data_root.text())
        if chosen:
            self.data_root.setText(chosen)

    def _role(self) -> QWidget:
        page, layout = self._page(tr("wizard.role_title"), tr("server.role_hint"))
        self.role_group = QButtonGroup(self)
        self.role_buttons: dict[Role, QRadioButton] = {}
        for role in (Role.STANDALONE, Role.GATE, Role.SERVER):
            button = QRadioButton(tr(f"wizard.role_{role.value}"))
            button.setChecked(role is Role.STANDALONE)
            self.role_group.addButton(button)
            self.role_buttons[role] = button
            layout.addWidget(button)
        grid = QGridLayout()
        self.gate_code = QSpinBox()
        self.gate_code.setRange(1, 9)
        self.gate_name = TextField(tr("wizard.gate_name"))
        grid.addWidget(label(tr("hardware.gate_code"), "caption"), 0, 0)
        grid.addWidget(self.gate_code, 1, 0)
        grid.addWidget(label(tr("wizard.gate_name"), "caption"), 0, 1)
        grid.addWidget(self.gate_name, 1, 1)
        layout.addLayout(grid)
        layout.addWidget(label(tr("hardware.gate_code_hint"), "muted", wrap=True))
        layout.addStretch(1)
        return page

    def _server(self) -> QWidget:
        page, layout = self._page(tr("server.connection"), tr("wizard.server_hint"))
        grid = QGridLayout()
        self.host = TextField(r"PC-SERVER\SQLEXPRESS", persian_digits=False)
        self.database = TextField("parking", persian_digits=False)
        self.database.setText("parking")
        self.driver = QComboBox()
        self.driver.setEditable(True)
        self.driver.addItems(ODBC_DRIVERS)
        self.windows_auth = QCheckBox(tr("server.windows_auth"))
        self.windows_auth.setChecked(True)
        self.sql_user = TextField(tr("server.user"), persian_digits=False)
        self.sql_password = TextField(tr("server.password"), persian_digits=False)
        self.sql_password.setEchoMode(TextField.EchoMode.Password)
        pairs = [
            ("server.host", self.host),
            ("server.database", self.database),
            ("server.driver", self.driver),
            ("server.user", self.sql_user),
            ("server.password", self.sql_password),
        ]
        for index, (key, widget) in enumerate(pairs):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        layout.addLayout(grid)
        layout.addWidget(self.windows_auth)
        test = QHBoxLayout()
        self.server_status = label("", "muted", wrap=True)
        test.addWidget(self.server_status, 1)
        test.addWidget(Button(tr("server.test"), "cable", on_click=self.test_server))
        layout.addLayout(test)
        layout.addStretch(1)
        return page

    def _devices(self) -> QWidget:
        page, layout = self._page(tr("wizard.devices_title"), tr("wizard.devices_hint"))
        grid = QGridLayout()
        self.printer_backend = QComboBox()
        for backend in PRINTER_BACKENDS:
            self.printer_backend.addItem(tr(f"hardware.backend_{backend}"), backend)
        self.printer_name = QComboBox()
        self.printer_name.setEditable(True)
        self.printer_name.addItems(installed_printers())
        self.scanner_mode = QComboBox()
        for mode in SCANNER_MODES:
            self.scanner_mode.addItem(tr(f"hardware.scanner_{mode}"), mode)
        self.camera_kind = QComboBox()
        for kind in ("off", "simulator"):
            self.camera_kind.addItem(tr(f"hardware.camera_kind_{kind}"), kind)
        pairs = [
            ("hardware.backend", self.printer_backend),
            ("hardware.printer_name", self.printer_name),
            ("hardware.scanner_mode", self.scanner_mode),
            ("hardware.cameras", self.camera_kind),
        ]
        for index, (key, widget) in enumerate(pairs):
            grid.addWidget(label(tr(key), "caption"), (index // 2) * 2, index % 2)
            grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def _admin(self) -> QWidget:
        page, layout = self._page(tr("admin.create_title"), tr("admin.create_hint"))
        self.admin_name = TextField(tr("user.display_name"))
        self.admin_username = TextField(tr("login.username"), persian_digits=False)
        self.admin_password = TextField(tr("login.password"), persian_digits=False)
        self.admin_password.setEchoMode(TextField.EchoMode.Password)
        self.admin_repeat = TextField(tr("user.password_repeat"), persian_digits=False)
        self.admin_repeat.setEchoMode(TextField.EchoMode.Password)
        for widget in (self.admin_name, self.admin_username, self.admin_password, self.admin_repeat):
            layout.addWidget(widget)
        layout.addStretch(1)
        return page

    def _done(self) -> QWidget:
        page, layout = self._page(tr("wizard.done_title"))
        self.summary = label("", wrap=True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- navigation
    def role(self) -> Role:
        return next(role for role, button in self.role_buttons.items() if button.isChecked())

    def visible_pages(self) -> list[str]:
        pages = ["welcome", "role"]
        if self.role() is not Role.STANDALONE:
            pages.append("server")
        pages.append("devices")
        if self.needs_admin:
            pages.append("admin")
        pages.append("done")
        return pages

    def show_page(self, name: str) -> None:
        self.current = name
        self.stack.setCurrentWidget(self.pages[name])
        pages = self.visible_pages()
        self.step_label.setText(tr("wizard.step", n=str(pages.index(name) + 1), total=str(len(pages))))
        self.back_button.setEnabled(name != pages[0])
        self.next_button.setText(tr("wizard.finish") if name == "done" else tr("wizard.next"))
        self.error.setText("")
        if name == "done":
            self.summary.setText(self._summary())

    def next(self) -> None:
        pages = self.visible_pages()
        if self.current == "done":
            try:
                validate(self.choices(), self.needs_admin)
            except SetupError as exc:
                self.error.setText(tr(str(exc)))
                return
            self.accept()
            return
        self.show_page(pages[pages.index(self.current) + 1])

    def back(self) -> None:
        pages = self.visible_pages()
        index = pages.index(self.current)
        if index > 0:
            self.show_page(pages[index - 1])

    def _summary(self) -> str:
        choices = self.choices()
        lines = [
            tr("wizard.sum_role", role=tr(f"wizard.role_{choices.role.value}")),
            tr("wizard.sum_root", path=str(choices.data_root)),
        ]
        if choices.role is not Role.SERVER:
            lines.append(tr("wizard.sum_gate", code=str(choices.gate_code), name=choices.gate_name or "—"))
        if choices.role is not Role.STANDALONE:
            lines.append(tr("wizard.sum_server", host=choices.server_host or "—"))
        if choices.role is Role.GATE:
            lines.append(tr("wizard.sum_join"))
        return "\n".join(lines)

    def test_server(self) -> bool:
        from caspian_parking.services.context import server_engine_for

        choices = self.choices()
        config = self.ctx.config
        config.server.host = choices.server_host
        config.server.database = choices.server_database
        config.server.driver = choices.server_driver
        config.server.windows_auth = choices.windows_auth
        config.server.user = choices.sql_user
        store = SecretStore(self.ctx.data_root.config)
        if choices.sql_password:
            from caspian_parking.services.context import SQL_PASSWORD_SECRET

            store.set(SQL_PASSWORD_SECRET, choices.sql_password)
        if not choices.server_host:
            self.server_status.setText(tr("server.no_host"))
            return False
        engine = server_engine_for(config, store)
        try:
            with engine.connect() as conn:
                version = str(conn.execute(text("SELECT @@VERSION")).scalar_one()).splitlines()[0]
        except Exception as exc:  # shown to the installer
            self.server_status.setText(tr("server.test_failed", error=str(exc).splitlines()[0][:160]))
            return False
        finally:
            engine.dispose()
        self.server_status.setText(tr("server.test_ok", version=version))
        return True

    def choices(self) -> SetupChoices:
        theme = next((m for m, b in self.theme_buttons.items() if b.isChecked()), THEME_DARK)
        return SetupChoices(
            data_root=Path(self.data_root.text().strip() or str(self.ctx.data_root.root)),
            role=self.role(),
            gate_code=self.gate_code.value(),
            gate_name=self.gate_name.value(),
            server_host=self.host.text().strip(),
            server_database=self.database.text().strip(),
            server_driver=self.driver.currentText().strip(),
            windows_auth=self.windows_auth.isChecked(),
            sql_user=self.sql_user.text().strip(),
            sql_password=self.sql_password.text(),
            printer_backend=self.printer_backend.currentData(),
            printer_name=self.printer_name.currentText().strip(),
            scanner_mode=self.scanner_mode.currentData(),
            camera_kind=self.camera_kind.currentData(),
            theme=theme,
            admin_username=self.admin_username.text().strip(),
            admin_name=self.admin_name.value(),
            admin_password=self.admin_password.text(),
            admin_password_again=self.admin_repeat.text(),
        )
