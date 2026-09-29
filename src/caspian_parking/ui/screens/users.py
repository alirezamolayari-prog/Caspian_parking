"""Users & permissions: list, edit form and the permission checkbox matrix (SPEC §4.1)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QWidget,
)

from caspian_parking.core.ids import uuid7
from caspian_parking.core.permissions import BUILTIN_PRESETS, Permission, normalize_permissions
from caspian_parking.data.models import RolePreset, User
from caspian_parking.data.repositories.base import StaleRecordError
from caspian_parking.data.repositories.system import RolePresetRepository, UserRepository
from caspian_parking.i18n import tr
from caspian_parking.services import auth
from caspian_parking.services.auth import AuthError, CurrentUser
from caspian_parking.services.context import AppContext
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import ModalDialog, show_toast
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel


class UsersScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("users.title"), tr("users.subtitle"))
        self.current: User | None = None
        self._preset_names: dict[str, str] = {}
        self.header_actions.addWidget(Button(tr("users.new"), "user-plus", variant="primary", on_click=self.new_user))
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)

        columns = [
            Column(tr("user.display_name"), lambda u: u.display_name, width=200),
            Column(tr("login.username"), lambda u: u.username, width=140),
            Column(tr("users.preset"), lambda u: self._preset_names.get(u.role_preset_code or "", "—")),
            Column(tr("users.status"), lambda u: tr("common.active") if u.is_active else tr("common.inactive")),
        ]
        self.model = LazyTableModel(columns, self._fetch)
        self.table = DataTable(self.model)
        self.table.clicked.connect(lambda _i: self._select(self.table.selected_object()))
        list_card = Card(tr("users.list"))
        list_card.add(self.table, 1)
        row.addWidget(list_card, 3)

        self.form = Card(tr("users.edit"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.M)
        grid.setVerticalSpacing(Space.S)
        self.display_name = TextField(tr("user.display_name"))
        self.username = TextField(tr("login.username"), persian_digits=False)
        self.password = TextField(tr("login.password"), persian_digits=False)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.active = QCheckBox(tr("common.active"))
        self.preset = QComboBox()
        self._updating = True
        self._load_presets()
        self._updating = False
        self.preset.currentIndexChanged.connect(self._apply_preset)
        grid.addWidget(label(tr("user.display_name"), "caption"), 0, 0)
        grid.addWidget(self.display_name, 1, 0)
        grid.addWidget(label(tr("login.username"), "caption"), 0, 1)
        grid.addWidget(self.username, 1, 1)
        grid.addWidget(label(tr("users.preset"), "caption"), 2, 0)
        grid.addWidget(self.preset, 3, 0)
        grid.addWidget(label(tr("login.password"), "caption"), 2, 1)
        grid.addWidget(self.password, 3, 1)
        grid.addWidget(self.active, 4, 0)
        self.form.body().addLayout(grid)
        self.form.add(label(tr("users.permissions"), "title"))
        matrix = QWidget()
        self.matrix = QGridLayout(matrix)
        self.matrix.setHorizontalSpacing(Space.XL)
        self.checks: dict[str, QCheckBox] = {}
        for index, permission in enumerate(Permission):
            box = QCheckBox(tr(f"perm.{permission.value}"))
            box.toggled.connect(self._mark_custom)
            self.checks[permission.value] = box
            self.matrix.addWidget(box, index // 2, index % 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(matrix)
        self.form.add(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(Button(tr("users.save_preset"), "list-checks", variant="ghost", on_click=self.save_as_preset))
        buttons.addStretch(1)
        self.save_button = Button(tr("common.save"), "check", variant="primary", on_click=self.save)
        buttons.addWidget(self.save_button)
        self.form.body().addLayout(buttons)
        row.addWidget(self.form, 4)
        self._updating = False
        self.new_user()

    # ---- presets ----------------------------------------------------------
    def _load_presets(self) -> None:
        with self.ctx.read() as session:
            presets = list(RolePresetRepository(session).active())
        self._preset_names = {p.code: p.name for p in presets}
        current = self.preset.currentData()
        self.preset.clear()
        self.preset.addItem(tr("users.custom"), "")
        for preset in presets:
            self.preset.addItem(preset.name, preset.code)
        self.preset.setCurrentIndex(max(0, self.preset.findData(current)))

    def save_as_preset(self, name: str | None = None) -> RolePreset | None:
        """Save the current checkbox matrix as a new named role preset."""
        if name is None:
            dialog = ModalDialog(self, tr("users.save_preset"), width=420)
            field = TextField(tr("users.preset_name"))
            dialog.content.addWidget(field)
            dialog.add_button(tr("common.cancel"), role="reject")
            dialog.add_button(tr("common.save"), variant="primary")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            name = field.value()
        if not name:
            show_toast(self, tr("users.preset_name_required"), "error")
            return None
        with self.ctx.uow() as session:
            preset = RolePresetRepository(session).add(
                RolePreset(code="custom-" + uuid7()[-12:], name=name, permissions=self.selected_permissions())
            )
        self._updating = True
        self._load_presets()
        self.preset.setCurrentIndex(self.preset.findData(preset.code))
        self._updating = False
        show_toast(self, tr("common.saved"))
        return preset

    # ---- data -----------------------------------------------------------
    def _fetch(self, offset: int, limit: int) -> list[User]:
        with self.ctx.read() as session:
            return list(UserRepository(session).page(offset, limit))

    def on_show(self) -> None:
        self.model.reset()

    def _select(self, user: User | None) -> None:
        if user is None:
            return
        self.current = user
        self._updating = True
        self.display_name.setText(user.display_name)
        self.username.setText(user.username)
        self.username.setReadOnly(True)
        self.password.clear()
        self.password.setPlaceholderText(tr("users.password_keep"))
        self.active.setChecked(user.is_active)
        index = self.preset.findData(user.role_preset_code or "")
        self.preset.setCurrentIndex(max(0, index))
        for code, box in self.checks.items():
            box.setChecked(code in (user.permissions or []))
        self._updating = False

    def new_user(self) -> None:
        self.current = None
        self._updating = True
        self.display_name.clear()
        self.username.clear()
        self.username.setReadOnly(False)
        self.password.clear()
        self.password.setPlaceholderText(tr("login.password"))
        self.active.setChecked(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData("operator")))
        self._updating = False
        self._apply_preset()
        self.display_name.setFocus()

    def _apply_preset(self, *_args: object) -> None:
        code = self.preset.currentData()
        if not code or self._updating:
            return
        with self.ctx.read() as session:
            preset = RolePresetRepository(session).by_code(code)
            permissions = set(preset.permissions if preset else BUILTIN_PRESETS.get(code, ()))
        self._updating = True
        for perm, box in self.checks.items():
            box.setChecked(perm in permissions)
        self._updating = False

    def _mark_custom(self, *_args: object) -> None:
        if not self._updating:
            self._updating = True
            self.preset.setCurrentIndex(0)
            self._updating = False

    def selected_permissions(self) -> list[str]:
        return normalize_permissions([code for code, box in self.checks.items() if box.isChecked()])

    # ---- save -----------------------------------------------------------
    def save(self) -> bool:
        permissions = self.selected_permissions()
        preset = self.preset.currentData() or None
        try:
            with self.ctx.uow() as session:
                if self.current is None:
                    user = auth.create_user(
                        session,
                        self.username.value(),
                        self.display_name.value(),
                        self.password.text(),
                        preset=preset,
                        permissions=permissions,
                        must_change_password=True,
                    )
                else:
                    existing = session.get(User, self.current.id)
                    assert existing is not None
                    user = existing
                    self._guard_self_lockout(session, user, permissions)
                    UserRepository(session).update(
                        user,
                        expected_version=self.current.row_version,
                        display_name=self.display_name.value() or user.display_name,
                        permissions=permissions,
                        role_preset_code=preset,
                        is_active=self.active.isChecked(),
                    )
                    if self.password.text():
                        auth.validate_new_password(self.password.text())
                        UserRepository(session).update(
                            user, password_hash=auth.hash_password(self.password.text()), must_change_password=True
                        )
                saved_id = user.id
        except AuthError as exc:
            show_toast(self, tr(str(exc)), "error")
            return False
        except StaleRecordError:
            show_toast(self, tr("common.stale"), "error")
            self.model.reset()
            return False
        if self.ctx.user is not None and saved_id == self.ctx.user.id:
            with self.ctx.read() as session:
                refreshed = session.get(User, saved_id)
                if refreshed is not None:
                    self.ctx.user = CurrentUser.from_user(refreshed)
        show_toast(self, tr("common.saved"))
        self.model.reset()
        with self.ctx.read() as session:
            self._select(session.get(User, saved_id))
        return True

    def _guard_self_lockout(self, session: object, user: User, permissions: list[str]) -> None:
        """An administrator may not remove their own user-management right or deactivate themselves."""
        if self.ctx.user is None or user.id != self.ctx.user.id:
            return
        if Permission.MANAGE_USERS not in permissions or not self.active.isChecked():
            raise AuthError("users.self_lockout")
