"""Ad slideshow window (SPEC §4.11): full screen on a second monitor when present, windowed otherwise.

New or removed files in ``ads\\slideshow`` are picked up automatically (QFileSystemWatcher).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from caspian_parking.devices.adscreen import SlideRotation
from caspian_parking.i18n import tr
from caspian_parking.ui.theme.tokens import Size

MS_PER_SECOND = 1000


class SlideshowWindow(QWidget):
    def __init__(
        self,
        folder: Path,
        seconds: int,
        parent: QWidget | None = None,
        texts: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("Slideshow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle(tr("adscreen.title"))
        self.rotation = SlideRotation(folder)
        self.image = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image, 1)
        self._pixmap: QPixmap | None = None
        self._texts_provider = texts or (list)
        self._text_queue: list[str] = []
        self._text_index = 0
        self.current_text: str | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(max(1, seconds) * MS_PER_SECOND)
        self.timer.timeout.connect(self.advance)
        folder.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher([str(folder)], self)
        self.watcher.directoryChanged.connect(self._folder_changed)
        self.advance()
        self.timer.start()

    @property
    def current(self) -> Path | None:
        return self.rotation.current

    def advance(self) -> Path | None:
        """Next image; after each full round of images the shop slides (ads with a screen package) follow."""
        if self._text_queue:
            self._show_text(self._text_queue.pop(0))
            return None
        path = self.rotation.next()
        if path is None:
            texts = self._texts_provider()
            if texts:
                self._show_text(texts[self._text_index % len(texts)])
                self._text_index += 1
                return None
        elif self.rotation.slides and path == self.rotation.slides[-1]:
            self._text_queue = list(self._texts_provider())
        self.current_text = None
        self._pixmap = QPixmap(str(path)) if path is not None else None
        if self._pixmap is not None and self._pixmap.isNull():
            self._pixmap = None
        self._paint()
        return path

    def _folder_changed(self, _path: str) -> None:
        before = self.rotation.current
        self.rotation.rescan()
        if self.rotation.current is None or before is None:
            self.advance()

    def _show_text(self, text: str) -> None:
        self.current_text = text
        self._pixmap = None
        self.image.setPixmap(QPixmap())
        self.image.setText(text)

    def _paint(self) -> None:
        if self.current_text is not None:
            return
        if self._pixmap is None:
            self.image.setPixmap(QPixmap())
            self.image.setText(tr("adscreen.empty"))
            return
        self.image.setText("")
        self.image.setPixmap(
            self._pixmap.scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._paint()

    def show_on_best_screen(self) -> None:
        """Full screen on the second monitor if there is one; otherwise a normal window."""
        screens = QGuiApplication.screens()
        if len(screens) > 1:
            target = screens[1]
            self.setGeometry(target.geometry())
            self.showFullScreen()
        else:
            self.resize(Size.SLIDESHOW_W, Size.SLIDESHOW_H)
            self.show()

    def set_seconds(self, seconds: int) -> None:
        self.timer.setInterval(max(1, seconds) * MS_PER_SECOND)
