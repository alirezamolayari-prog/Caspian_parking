"""Barcode scanners (SPEC §6): USB keyboard-wedge (burst detection), serial COM, simulator.

Keyboard-wedge scanners "type" very fast (a few ms per key) and end with Enter. The filter
holds the first key for up to ``max_gap_ms``: if the next key arrives that fast, the keys are a
scan (swallowed and emitted as one string, whatever widget has focus); otherwise the held key is
replayed to its widget so normal typing is not affected.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from caspian_parking.core.digits import normalize_input

log = logging.getLogger(__name__)

DEFAULT_MAX_GAP_MS = 35
DEFAULT_MIN_LENGTH = 6
_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_BLOCKING_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
)


class ScannerSource(QObject):
    """Base: emits ``scanned`` with the normalized text (Latin digits)."""

    scanned = Signal(str)

    def emit_scan(self, raw: str) -> None:
        text = normalize_input(raw)
        if text:
            self.scanned.emit(text)


class SimulatorScanner(ScannerSource):
    def feed(self, text: str) -> None:
        self.emit_scan(text)


class WedgeScanner(ScannerSource):
    """Application-wide key filter that recognises scanner bursts."""

    def __init__(
        self,
        max_gap_ms: int = DEFAULT_MAX_GAP_MS,
        min_length: int = DEFAULT_MIN_LENGTH,
        clock_ms: Callable[[], float] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.max_gap_ms = max_gap_ms
        self.min_length = min_length
        self._clock_ms = clock_ms or (lambda: time.monotonic() * 1000)
        self._buffer = ""
        self._burst = False
        self._last = 0.0
        self._held: tuple[QObject, QKeyEvent] | None = None
        self._replaying = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.timeout)
        self._installed = False

    def install(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._installed:
            app.installEventFilter(self)
            self._installed = True

    def uninstall(self) -> None:
        app = QApplication.instance()
        if app is not None and self._installed:
            app.removeEventFilter(self)
            self._installed = False

    # ---------------------------------------------------------------- core logic (testable)
    def handle_key(self, target: QObject, event: QKeyEvent) -> bool:
        """Return True when the key is consumed by the scanner logic."""
        now = self._clock_ms()
        if event.key() in _ENTER_KEYS:
            if self._burst:
                self._finish()
                return True
            self._replay_held()
            return False
        text = event.text()
        if len(text) != 1 or not text.isprintable() or event.modifiers() & _BLOCKING_MODIFIERS:
            self._replay_held()
            return False
        fast = self._buffer and (now - self._last) <= self.max_gap_ms
        if fast:
            self._burst = True
            self._held = None  # the held first key belongs to the scan
            self._buffer += text
            self._last = now
            self._timer.start(self.max_gap_ms * 4)
            return True
        # a new sequence starts: hold this key briefly
        self._replay_held()
        self._buffer = text
        self._burst = False
        self._last = now
        self._held = (target, QKeyEvent(event.type(), event.key(), event.modifiers(), text))
        self._timer.start(self.max_gap_ms + 5)
        return True

    def timeout(self) -> None:
        if self._burst:
            self._finish()
        else:
            self._replay_held()

    def _finish(self) -> None:
        self._timer.stop()
        text, long_enough = self._buffer, len(self._buffer) >= self.min_length
        self._buffer = ""
        self._burst = False
        self._held = None
        if long_enough:
            self.emit_scan(text)
        else:
            log.debug("ignored short burst %r", text)

    def _replay_held(self) -> None:
        self._timer.stop()
        held, self._held = self._held, None
        if not self._burst:
            self._buffer = ""
        if held is None:
            return
        target, event = held
        self._replaying = True
        try:
            QApplication.sendEvent(target, event)
        finally:
            self._replaying = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._replaying or event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        owner = self.parent()
        if isinstance(owner, QWidget) and not owner.isVisible():
            return False  # only listen while the owning screen is on screen
        if event.isAutoRepeat():
            return False
        return self.handle_key(watched, event)


class SerialScanner(ScannerSource):
    """Scanner on a COM port: one code per line (CR or LF). Reconnects automatically."""

    _line = Signal(str)

    def __init__(self, port: str, baud: int = 9600, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self._line.connect(self.emit_scan, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"scanner-{self.port}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:  # pragma: no cover - needs hardware
        import serial

        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.5) as handle:
                    self.connected = True
                    buffer = b""
                    while not self._stop.is_set():
                        chunk = handle.read(64)
                        if not chunk:
                            continue
                        buffer += chunk
                        while b"\r" in buffer or b"\n" in buffer:
                            index = min(i for i in (buffer.find(b"\r"), buffer.find(b"\n")) if i >= 0)
                            line, buffer = buffer[:index], buffer[index + 1 :]
                            if line.strip():
                                self._line.emit(line.decode("utf-8", errors="ignore"))
            except Exception as exc:
                self.connected = False
                log.warning("serial scanner %s unavailable: %s", self.port, exc)
                self._stop.wait(3)
