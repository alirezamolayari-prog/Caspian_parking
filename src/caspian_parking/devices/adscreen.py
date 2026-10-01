"""Ad display screen (SPEC §4.11, §6): optional second monitor showing a slideshow of ``ads\\slideshow``.

``SlideRotation`` is the pure part (which slide is next, following folder changes). ``SimulatorAdScreen``
records what would be shown; the Qt window lives in ``ui.widgets.slideshow``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

SLIDE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")


def list_slides(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SLIDE_EXTENSIONS),
        key=lambda p: p.name.casefold(),
    )


class SlideRotation:
    """Cycles through the slides in name order; a re-scan keeps the position by file name."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.slides: list[Path] = []
        self._current: Path | None = None
        self.rescan()

    def rescan(self) -> None:
        self.slides = list_slides(self.folder)
        if self._current not in self.slides:
            self._current = None

    def next(self) -> Path | None:
        if not self.slides:
            self._current = None
            return None
        if self._current is None:
            self._current = self.slides[0]
        else:
            index = self.slides.index(self._current)
            self._current = self.slides[(index + 1) % len(self.slides)]
        return self._current

    @property
    def current(self) -> Path | None:
        return self._current


class AdScreen(Protocol):
    def show_slide(self, path: Path | None) -> None: ...

    def close(self) -> None: ...


class SimulatorAdScreen:
    def __init__(self) -> None:
        self.shown: list[Path | None] = []
        self.closed = False

    def show_slide(self, path: Path | None) -> None:
        self.shown.append(path)

    def close(self) -> None:
        self.closed = True
