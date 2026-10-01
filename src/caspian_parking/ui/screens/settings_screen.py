"""Settings: appearance (per user) and site basics (mall name, gates, levels & capacities)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import Gate, Level, User
from caspian_parking.data.repositories.system import GateRepository, LevelRepository, UserRepository
from caspian_parking.i18n import tr
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting, set_setting
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.screens.settings_devices import HardwareTab, ReceiptTab
from caspian_parking.ui.screens.settings_server import ServerTab
from caspian_parking.ui.theme.manager import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, ThemeManager
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Button, Card, TextField, label
from caspian_parking.ui.widgets.feedback import show_toast


class SettingsScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("settings.title"), tr("settings.subtitle"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._appearance_tab(), tr("settings.appearance"))
        if ctx.can(Permission.CHANGE_SETTINGS):
            self.tabs.addTab(self._site_tab(), tr("settings.site"))
            self.receipt_tab = ReceiptTab(ctx)
            self.tabs.addTab(self.receipt_tab, tr("settings.receipt"))
        if ctx.can(Permission.HARDWARE_SETTINGS):
            self.hardware_tab = HardwareTab(ctx)
            self.tabs.addTab(self.hardware_tab, tr("settings.hardware"))
        if ctx.can(Permission.HARDWARE_SETTINGS) and ctx.can(Permission.CHANGE_SETTINGS):
            self.server_tab = ServerTab(ctx)
            self.tabs.addTab(self.server_tab, tr("settings.server"))
        self.body.addWidget(self.tabs, 1)

    # ---- appearance -----------------------------------------------------
    def _appearance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        card = Card(tr("settings.theme"), shadow=False)
        self.theme_group = QButtonGroup(self)
        current = self.ctx.user.theme if self.ctx.user and self.ctx.user.theme else ThemeManager.instance().mode
        self.theme_buttons: dict[str, QRadioButton] = {}
        for mode in (THEME_DARK, THEME_LIGHT, THEME_SYSTEM):
            button = QRadioButton(tr(f"theme.{mode}"))
            button.setChecked(mode == current)
            button.toggled.connect(lambda checked, m=mode: self._theme_toggled(checked, m))
            self.theme_group.addButton(button)
            self.theme_buttons[mode] = button
            card.add(button)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _theme_toggled(self, checked: bool, mode: str) -> None:
        if checked:
            self.set_theme(mode)

    def set_theme(self, mode: str) -> None:
        ThemeManager.instance().apply(mode)
        if self.ctx.user is None:
            return
        with self.ctx.uow() as session:
            user = session.get(User, self.ctx.user.id)
            if user is not None:
                UserRepository(session).update(user, theme=mode)

    # ---- site -------------------------------------------------------------
    def _site_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.L)
        with self.ctx.read() as session:
            mall = get_setting(session, "site.mall_name")
            gates = list(GateRepository(session).page(0, 50))
            levels = list(LevelRepository(session).page(0, 50))

        mall_card = Card(tr("settings.mall"), shadow=False)
        self.mall_name = TextField(tr("settings.mall_name"))
        self.mall_name.setText(mall)
        mall_card.add(self.mall_name)
        layout.addWidget(mall_card)

        gates_card = Card(tr("settings.gates"), shadow=False)
        gate_grid = QGridLayout()
        gate_grid.setHorizontalSpacing(Space.M)
        self.gate_fields: dict[str, TextField] = {}
        for row, gate in enumerate(gates):
            gate_grid.addWidget(label(tr("settings.gate_code", code=str(gate.code)), "muted"), row, 0)
            field = TextField()
            field.setText(gate.name)
            self.gate_fields[gate.id] = field
            gate_grid.addWidget(field, row, 1)
        gates_card.body().addLayout(gate_grid)
        layout.addWidget(gates_card)

        levels_card = Card(tr("settings.levels"), shadow=False)
        level_grid = QGridLayout()
        level_grid.setHorizontalSpacing(Space.M)
        headers = ("settings.level_name", "settings.capacity", "settings.is_parking", "settings.is_open")
        for column, key in enumerate(headers):
            level_grid.addWidget(label(tr(key), "caption"), 0, column)
        self.level_fields: dict[str, tuple[TextField, QSpinBox, QCheckBox, QCheckBox]] = {}
        for row, level in enumerate(levels, start=1):
            name = TextField()
            name.setText(level.name)
            capacity = QSpinBox()
            capacity.setRange(0, 5000)
            capacity.setValue(level.capacity)
            parking = QCheckBox()
            parking.setChecked(level.is_parking)
            is_open = QCheckBox()
            is_open.setChecked(level.is_open)
            for column, widget in enumerate((name, capacity, parking, is_open)):
                level_grid.addWidget(widget, row, column)
            self.level_fields[level.id] = (name, capacity, parking, is_open)
        levels_card.body().addLayout(level_grid)
        layout.addWidget(levels_card)
        save = Button(tr("common.save"), "check", variant="primary", on_click=self.save_site)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def save_site(self) -> None:
        with self.ctx.uow(reason=tr("settings.site")) as session:
            set_setting(session, "site.mall_name", self.mall_name.value())
            gates = GateRepository(session)
            for gate_id, field in self.gate_fields.items():
                gate = session.get(Gate, gate_id)
                if gate is not None and field.value() and field.value() != gate.name:
                    gates.update(gate, name=field.value())
            levels = LevelRepository(session)
            for level_id, (name, capacity, parking, is_open) in self.level_fields.items():
                level = session.get(Level, level_id)
                if level is None:
                    continue
                changes: dict[str, Any] = {
                    "name": name.value() or level.name,
                    "capacity": capacity.value(),
                    "is_parking": parking.isChecked(),
                    "is_open": is_open.isChecked(),
                }
                if any(getattr(level, k) != v for k, v in changes.items()):
                    levels.update(level, **changes)
        show_toast(self, tr("common.saved"))
