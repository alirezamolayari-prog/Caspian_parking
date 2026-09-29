"""Main window: right-hand sidebar, top bar, alert bar and the screen stack."""

from __future__ import annotations

import functools
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from caspian_parking.config.defaults import product_name
from caspian_parking.config.machine import Role
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_long_date, fa_time
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting
from caspian_parking.ui.shell.command_palette import CommandPalette, PaletteItem, Provider, matches
from caspian_parking.ui.shell.registry import ScreenSpec, visible_screens
from caspian_parking.ui.theme.icons import icon, icon_size
from caspian_parking.ui.theme.manager import ThemeManager, set_dark_title_bar
from caspian_parking.ui.theme.tokens import Motion, Size, Space
from caspian_parking.ui.widgets.basics import Button, chip, label, set_chip
from caspian_parking.ui.widgets.feedback import AlertBar, ModalDialog


class Sidebar(QWidget):
    navigate = Signal(str)

    def __init__(self, screens: list[ScreenSpec], title: str) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._collapsed = False
        self._screens = screens
        self.setFixedWidth(Size.SIDEBAR)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.L, Space.M, Space.L)
        layout.setSpacing(Space.XS)
        self.title = label(title, "title", wrap=True)
        layout.addWidget(self.title)
        layout.addSpacing(Space.M)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, Button] = {}
        for spec in screens:
            button = Button(tr(spec.title_key), spec.icon)
            button.setProperty("nav", "true")
            button.setCheckable(True)
            button.setToolTip(tr(spec.title_key))
            button.clicked.connect(lambda _c=False, key=spec.key: self.navigate.emit(key))
            self.group.addButton(button)
            self.buttons[spec.key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.collapse_button = Button(tr("nav.collapse"), "panel-right-close", variant="ghost")
        self.collapse_button.setProperty("nav", "true")
        self.collapse_button.clicked.connect(self.toggle)
        layout.addWidget(self.collapse_button)
        self._animation = QPropertyAnimation(self, b"minimumWidth", self)
        self._animation.setDuration(Motion.SLOW)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: self.setMaximumWidth(int(value)))

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        widths = (Size.SIDEBAR, Size.SIDEBAR_COLLAPSED)
        start, end = widths if self._collapsed else widths[::-1]
        self.setMinimumWidth(0)
        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()
        for spec in self._screens:
            self.buttons[spec.key].setText("" if self._collapsed else tr(spec.title_key))
        self.title.setVisible(not self._collapsed)
        self.collapse_button.setText("" if self._collapsed else tr("nav.collapse"))
        self.collapse_button._icon_name = "panel-right-open" if self._collapsed else "panel-right-close"
        self.collapse_button._refresh_icon()


class TopBar(QWidget):
    def __init__(self, ctx: AppContext, on_search: Callable[[], object], on_theme: Callable[[], object]) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(Size.TOPBAR)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.L, 0, Space.L, 0)
        layout.setSpacing(Space.M)
        self.gate_chip = chip("", "accent")
        layout.addWidget(self.gate_chip)
        self.link_chip = chip("", "neutral")
        layout.addWidget(self.link_chip)
        layout.addStretch(1)
        self.search_button = Button(tr("topbar.search"), "search", variant="ghost", on_click=on_search)
        self.search_button.setToolTip(tr("topbar.search_tip"))
        layout.addWidget(self.search_button)
        self.clock = label("")
        self.clock.setObjectName("Clock")
        layout.addWidget(self.clock)
        self.date = label("", "muted")
        layout.addWidget(self.date)
        self.theme_button = QToolButton()
        self.theme_button.setProperty("variant", "ghost")
        self.theme_button.setToolTip(tr("topbar.theme"))
        self.theme_button.clicked.connect(on_theme)
        layout.addWidget(self.theme_button)
        self.user_button = QToolButton()
        self.user_button.setProperty("variant", "ghost")
        self.user_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.user_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.user_menu = QMenu(self.user_button)
        self.user_button.setMenu(self.user_menu)
        layout.addWidget(self.user_button)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(1000)
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh()

    def refresh(self) -> None:
        config = self.ctx.config
        with self.ctx.read() as session:
            from caspian_parking.data.repositories.system import GateRepository

            gate = GateRepository(session).by_code(config.gate_code) if config.gate_code is not None else None
        role_text = tr(f"role_machine.{config.role.value}")
        self.gate_chip.setText(gate.name if gate else role_text)
        self.set_link_status(None if config.role is Role.STANDALONE else False)
        if self.ctx.user:
            self.user_button.setText(self.ctx.user.display_name)
        self.refresh_icons()
        self.tick()

    def set_link_status(self, connected: bool | None, last_sync: str | None = None) -> None:
        if connected is None:
            set_chip(self.link_chip, tr("link.standalone"), "neutral")
        elif connected:
            text = tr("link.connected") + (f" · {last_sync}" if last_sync else "")
            set_chip(self.link_chip, text, "success")
        else:
            set_chip(self.link_chip, tr("link.disconnected"), "danger")

    def refresh_icons(self, *_args: object) -> None:
        dark = ThemeManager.instance().palette.is_dark
        self.theme_button.setIcon(icon("sun" if dark else "moon", "text"))
        self.theme_button.setIconSize(icon_size())
        self.user_button.setIcon(icon("user", "text"))

    def tick(self) -> None:
        now = self.ctx.clock.now_utc()
        self.clock.setText(fa_time(now, seconds=True))
        self.date.setText(fa_long_date(now))


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.extra_providers: list[Provider] = []
        with ctx.read() as session:
            mall = get_setting(session, "site.mall_name") or product_name()
        self.setWindowTitle(mall)
        self.resize(1366, 820)
        root = QWidget()
        root.setObjectName("AppRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if ctx.training:
            banner = label(tr("training.banner"))
            banner.setObjectName("TrainingBanner")
            banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(banner)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.screens = visible_screens(ctx.can)
        self.sidebar = Sidebar(self.screens, mall)
        self.sidebar.navigate.connect(self.show_screen)
        body.addWidget(self.sidebar)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.topbar = TopBar(ctx, self.open_palette, self.toggle_theme)
        column.addWidget(self.topbar)
        self.alerts = AlertBar()
        column.addWidget(self.alerts)
        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        body.addLayout(column, 1)
        self.setCentralWidget(root)
        self._pages: dict[str, QWidget] = {}
        self._build_user_menu()
        self._shortcuts()
        if self.screens:
            self.show_screen(self.screens[0].key)

    # ---- navigation ---------------------------------------------------
    def current_key(self) -> str | None:
        for key, page in self._pages.items():
            if page is self.stack.currentWidget():
                return key
        return None

    def page(self, key: str) -> QWidget | None:
        return self._pages.get(key)

    def show_screen(self, key: str) -> QWidget | None:
        spec = next((s for s in self.screens if s.key == key), None)
        if spec is None:
            return None
        page = self._pages.get(key)
        if page is None:
            page = spec.factory(self.ctx)
            page.setObjectName("ScreenRoot")
            self._pages[key] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.sidebar.select(key)
        refresh = getattr(page, "on_show", None)
        if callable(refresh):
            refresh()
        return page

    # ---- palette / shortcuts -------------------------------------------
    def palette_providers(self) -> list[Provider]:
        def screens_provider(query: str) -> list[PaletteItem]:
            return [
                PaletteItem(
                    tr(spec.title_key),
                    tr("palette.screen"),
                    spec.icon,
                    functools.partial(self.show_screen, spec.key),
                    " ".join(spec.keywords),
                )
                for spec in self.screens
                if matches(query, tr(spec.title_key), *spec.keywords)
            ]

        return [screens_provider, *self.extra_providers]

    def open_palette(self) -> CommandPalette:
        palette = CommandPalette(self, self.palette_providers())
        palette.open()
        return palette

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_palette)
        QShortcut(QKeySequence("F1"), self, self.show_shortcuts)
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, self.toggle_theme)
        for spec in self.screens:
            if spec.shortcut:
                QShortcut(QKeySequence(spec.shortcut), self, lambda k=spec.key: self.show_screen(k))

    def show_shortcuts(self) -> None:
        dialog = ModalDialog(self, tr("shortcuts.title"), width=560)
        rows = [
            ("Ctrl+K", tr("shortcuts.search")),
            ("F1", tr("shortcuts.help")),
            ("Ctrl+Shift+T", tr("shortcuts.theme")),
        ]
        rows += [(spec.shortcut, tr(spec.title_key)) for spec in self.screens if spec.shortcut]
        rows += [(key, tr(text)) for key, text in extra_shortcut_rows()]
        for keys, text in rows:
            line = QHBoxLayout()
            line.addWidget(label(text), 1)
            line.addWidget(chip(keys, "neutral"))
            dialog.content.addLayout(line)
        dialog.add_button(tr("common.close"), role="accept")
        dialog.open()

    # ---- theme / user ----------------------------------------------------
    def toggle_theme(self) -> None:
        palette = ThemeManager.instance().toggle()
        set_dark_title_bar(self, palette.is_dark)
        if self.ctx.user is not None:
            from caspian_parking.data.models import User
            from caspian_parking.data.repositories.system import UserRepository

            with self.ctx.uow() as session:
                user = session.get(User, self.ctx.user.id)
                if user is not None:
                    UserRepository(session).update(user, theme=palette.name)

    def _build_user_menu(self) -> None:
        menu = self.topbar.user_menu
        menu.clear()
        menu.addAction(icon("key-round"), tr("user.change_password"), self._change_password)
        menu.addAction(icon("keyboard"), tr("shortcuts.title"), self.show_shortcuts)
        menu.addSeparator()
        menu.addAction(icon("log-out"), tr("user.logout"), self.logout_requested.emit)

    def _change_password(self) -> None:
        from caspian_parking.ui.shell.login import ChangePasswordDialog

        if self.ctx.user is not None:
            ChangePasswordDialog(self.ctx, self.ctx.user.id, parent=self).exec()

    def showEvent(self, event: object) -> None:
        set_dark_title_bar(self, ThemeManager.instance().palette.is_dark)
        super().showEvent(event)  # type: ignore[arg-type]


_EXTRA_SHORTCUTS: list[tuple[str, str]] = []


def add_shortcut_row(keys: str, text_key: str) -> None:
    """Screens register their F-keys here so Help → Shortcuts lists them."""
    if (keys, text_key) not in _EXTRA_SHORTCUTS:
        _EXTRA_SHORTCUTS.append((keys, text_key))


def extra_shortcut_rows() -> list[tuple[str, str]]:
    return list(_EXTRA_SHORTCUTS)
