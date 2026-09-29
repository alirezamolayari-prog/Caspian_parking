"""Ctrl+K command palette / global search (SPEC §3). Providers plug in results."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QFrame, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from caspian_parking.core.digits import normalize_input
from caspian_parking.i18n import tr
from caspian_parking.ui.theme.icons import icon
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import TextField, label

MAX_RESULTS = 30


@dataclass(frozen=True)
class PaletteItem:
    title: str
    subtitle: str
    icon: str
    action: Callable[[], object]
    keywords: str = ""


Provider = Callable[[str], Sequence[PaletteItem]]


def matches(query: str, *texts: str) -> bool:
    """Case/digit-insensitive 'all words appear' match."""
    haystack = normalize_input(" ".join(texts)).casefold()
    words = normalize_input(query).casefold().split()
    return all(word in haystack for word in words)


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget, providers: Sequence[Provider]) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._providers = list(providers)
        frame = QFrame(self)
        frame.setObjectName("Palette")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.S)
        self.search = TextField(tr("palette.placeholder"))
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._activate)
        layout.addWidget(self.results, 1)
        self.hint = label(tr("palette.hint"), "caption")
        layout.addWidget(self.hint)
        self.search.textChanged.connect(self.refresh)
        self.resize(640, 460)
        self.refresh()

    def showEvent(self, event: object) -> None:
        parent = self.parentWidget()
        if parent is not None:
            window = parent.window()
            geo = window.geometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + geo.height() // 6)
        self.search.setFocus()
        super().showEvent(event)  # type: ignore[arg-type]

    def refresh(self, *_args: object) -> None:
        query = self.search.value()
        self.results.clear()
        items: list[PaletteItem] = []
        for provider in self._providers:
            items.extend(provider(query))
            if len(items) >= MAX_RESULTS:
                break
        for entry in items[:MAX_RESULTS]:
            row = QListWidgetItem(icon(entry.icon, "text_muted"), f"{entry.title}    {entry.subtitle}".strip())
            row.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(row)
        if self.results.count():
            self.results.setCurrentRow(0)

    def item_count(self) -> int:
        return self.results.count()

    def _activate(self, row: QListWidgetItem) -> None:
        entry: PaletteItem = row.data(Qt.ItemDataRole.UserRole)
        self.accept()
        entry.action()

    def activate_current(self) -> None:
        row = self.results.currentItem()
        if row is not None:
            self._activate(row)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                row = max(0, min(self.results.count() - 1, self.results.currentRow() + step))
                self.results.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.activate_current()
                return True
        return super().eventFilter(watched, event)
