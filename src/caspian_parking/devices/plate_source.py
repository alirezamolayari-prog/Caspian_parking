"""Plate sources (SPEC §6): where plates come from at a lane.

* ``ManualPlateSource`` – no camera; the operator types the plate.
* ``SimulatorPlateSource`` – fake passes with configurable misreads (tests, demos, training).
* ``RtspPlateSource`` – RTSP/ONVIF stream read by OpenCV on a worker thread, frames sent to the ANPR
  engine, several frames of one pass voted into one plate; reconnects by itself.
* ``SmartCameraSource`` – ANPR cameras that read the plate themselves and push it over HTTP (JSON).

Every source emits ``passed(PlatePass)`` and ``frame(QImage)`` for the live preview, and
``online_changed(bool)`` for the alert bar. Signals emitted from worker threads are queued to the UI.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from caspian_parking.config.machine import CameraConfig
from caspian_parking.core.anpr import FrameRead, VoteResult, vote
from caspian_parking.core.clock import Clock
from caspian_parking.devices.anpr import AnprEngine, NullEngine

log = logging.getLogger(__name__)

PREVIEW_INTERVAL_S = 0.2  # ~5 fps preview is enough for the operator
RECONNECT_MAX_S = 30.0
PASS_GAP_FRAMES = 4  # frames without a plate that end a pass
PASS_MAX_FRAMES = 25
SNAPSHOT_SIZE = (640, 360)


@dataclass(frozen=True)
class PlatePass:
    lane: str
    camera: str
    result: VoteResult
    at: datetime
    snapshot: bytes | None = None  # JPEG


def safe_url(url: str) -> str:
    """Stream URL without user name / password (for logs and the UI)."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        parts = parts._replace(netloc=host)
    return urlunsplit(parts)


def image_to_jpeg(image: QImage, quality: int = 85) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", quality)  # type: ignore[call-overload]  # PySide stubs want bytes, runtime wants str
    return bytes(buffer.data().data())


class PlateSource(QObject):
    passed = Signal(object)  # PlatePass
    frame = Signal(QImage)
    online_changed = Signal(bool)

    def __init__(self, config: CameraConfig, clock: Clock, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.clock = clock
        self._online = False
        self.last_pass: PlatePass | None = None

    @property
    def name(self) -> str:
        return self.config.name or self.config.lane

    @property
    def online(self) -> bool:
        return self._online

    @property
    def threshold(self) -> float:
        return self.config.min_confidence / 100

    def _set_online(self, online: bool) -> None:
        if online != self._online:
            self._online = online
            self.online_changed.emit(online)

    def _emit_pass(self, result: VoteResult, snapshot: bytes | None) -> PlatePass:
        item = PlatePass(self.config.lane, self.name, result, self.clock.now_utc(), snapshot)
        self.last_pass = item
        self.passed.emit(item)
        return item

    def start(self) -> None:
        self._set_online(True)

    def stop(self) -> None:
        self._set_online(False)


class ManualPlateSource(PlateSource):
    """No camera: nothing is ever emitted."""

    def start(self) -> None:
        self._set_online(False)


class SimulatorPlateSource(PlateSource):
    """Fake camera: ``simulate("12ب345-22")`` produces a pass with several frames and random misreads."""

    def __init__(
        self,
        config: CameraConfig,
        clock: Clock,
        misread_rate: float = 0.0,
        frames: int = 5,
        seed: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(config, clock, parent)
        self.misread_rate = misread_rate
        self.frames = frames
        self.rng = random.Random(seed)

    def _misread(self, text: str) -> str:
        chars = list(text)
        digits = [i for i, c in enumerate(chars) if c.isdigit()]
        if digits and self.rng.random() < self.misread_rate:
            index = self.rng.choice(digits)
            chars[index] = str((int(chars[index]) + 1 + self.rng.randrange(8)) % 10)
        return "".join(chars)

    def simulate(self, plate_text: str | None, vehicle_type: str = "sedan", confidence: float = 0.92) -> PlatePass:
        reads = [
            FrameRead(self._misread(plate_text) if plate_text else None, confidence, vehicle_type, 0.9)
            for _ in range(self.frames)
        ]
        image = self._snapshot(plate_text or "?")
        self.frame.emit(image)
        return self._emit_pass(vote(reads, self.threshold), image_to_jpeg(image))

    def set_online(self, online: bool) -> None:
        self._set_online(online)

    @staticmethod
    def _snapshot(text: str) -> QImage:
        """A plain grey 'photo' with the plate text (simulator only; never shown as a real photo)."""
        image = QImage(*SNAPSHOT_SIZE, QImage.Format.Format_RGB32)
        image.fill(QColor(90, 96, 104))
        painter = QPainter(image)
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setPixelSize(48)
        painter.setFont(font)
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return image


@dataclass
class PassCollector:
    """Groups engine reads of consecutive frames into one pass (the vehicle in view)."""

    gap_frames: int = PASS_GAP_FRAMES
    max_frames: int = PASS_MAX_FRAMES
    reads: list[FrameRead] = field(default_factory=list)
    best: tuple[float, Any] | None = None
    _empty: int = 0

    def feed(self, reads: list[FrameRead], frame: Any) -> tuple[list[FrameRead], Any] | None:
        """Add one frame's reads; returns (reads, best frame) when a pass has finished."""
        if reads:
            self._empty = 0
            self.reads.extend(reads)
            top = max(r.confidence for r in reads)
            if self.best is None or top > self.best[0]:
                self.best = (top, frame)
            if len(self.reads) >= self.max_frames:
                return self._finish()
            return None
        if self.reads:
            self._empty += 1
            if self._empty >= self.gap_frames:
                return self._finish()
        return None

    def _finish(self) -> tuple[list[FrameRead], Any]:
        done = (self.reads, self.best[1] if self.best else None)
        self.reads, self.best, self._empty = [], None, 0
        return done


def ndarray_to_qimage(frame: Any) -> QImage:
    """OpenCV BGR frame → QImage (copied, safe to use after the frame buffer is reused)."""
    height, width = frame.shape[:2]
    channels = frame.shape[2] if frame.ndim == 3 else 1
    if channels == 1:
        return QImage(frame.data, width, height, width, QImage.Format.Format_Grayscale8).copy()
    return QImage(frame.data, width, height, channels * width, QImage.Format.Format_BGR888).copy()


def _opencv_capture(url: str) -> Any:
    import cv2

    return cv2.VideoCapture(url, cv2.CAP_FFMPEG)


def _encode_jpeg(frame: Any) -> bytes | None:
    try:
        import cv2

        ok, data = cv2.imencode(".jpg", frame)
        return data.tobytes() if ok else None
    except Exception:  # pragma: no cover - snapshot is optional
        return None


class RtspPlateSource(PlateSource):
    """RTSP/ONVIF camera on a worker thread with automatic reconnect (no operator action needed)."""

    def __init__(
        self,
        config: CameraConfig,
        clock: Clock,
        engine: AnprEngine | None = None,
        capture_factory: Callable[[str], Any] = _opencv_capture,
        sleep: Callable[[float], None] = time.sleep,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(config, clock, parent)
        self.engine = engine or NullEngine()
        self.capture_factory = capture_factory
        self.sleep = sleep
        self.collector = PassCollector()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.reconnects = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.config.lane}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._set_online(False)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            capture = None
            try:
                capture = self.capture_factory(self.config.url)
                if capture is None or not capture.isOpened():
                    raise OSError("stream not available")
                self._set_online(True)
                backoff = 1.0
                self._read_loop(capture)
            except Exception as exc:
                log.warning("camera %s (%s): %s", self.name, safe_url(self.config.url), exc)
            finally:
                if capture is not None:
                    with contextlib.suppress(Exception):
                        capture.release()
            self._set_online(False)
            if self._stop.is_set():
                break
            self.reconnects += 1
            self.sleep(backoff)
            backoff = min(RECONNECT_MAX_S, backoff * 2)

    def _read_loop(self, capture: Any) -> None:
        last_preview = 0.0
        while not self._stop.is_set():
            ok, frame = capture.read()
            if not ok or frame is None:
                raise OSError("stream interrupted")
            now = time.monotonic()
            if now - last_preview >= PREVIEW_INTERVAL_S:
                last_preview = now
                self.frame.emit(ndarray_to_qimage(frame))
            self.process_frame(frame)

    def process_frame(self, frame: Any) -> PlatePass | None:
        try:
            reads = self.engine.read(frame)
        except Exception:  # a failing engine must not kill the stream
            log.exception("ANPR engine failed on a frame")
            reads = []
        finished = self.collector.feed(reads, frame)
        if finished is None:
            return None
        pass_reads, best = finished
        snapshot = _encode_jpeg(best) if best is not None else None
        return self._emit_pass(vote(pass_reads, self.threshold), snapshot)


class _PushHandler(BaseHTTPRequestHandler):
    source: SmartCameraSource

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            self.source.handle_push(data)
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            self._reply(400, {"ok": False, "error": str(exc)})
            return
        self._reply(200, {"ok": True})

    def _reply(self, code: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("smart camera push: " + format, *args)


class SmartCameraSource(PlateSource):
    """Listens for HTTP POSTs from an ANPR camera.

    Body (JSON): ``{"plate": "12ب345-22", "confidence": 0.93, "vehicle_type": "sedan", "image": "<base64 JPEG>"}``
    or several frames: ``{"reads": [{"plate": …, "confidence": …}, …], "image": …}``. ``plate`` may be null
    (vehicle seen, plate unreadable).
    """

    def __init__(self, config: CameraConfig, clock: Clock, host: str = "0.0.0.0", parent: QObject | None = None):
        super().__init__(config, clock, parent)
        self.host = host
        self.server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.server.server_address[1] if self.server is not None else self.config.port

    def start(self) -> None:
        if self.server is not None:
            return
        handler = type("Handler", (_PushHandler,), {"source": self})
        try:
            self.server = ThreadingHTTPServer((self.host, self.config.port), handler)
        except OSError as exc:
            log.warning("smart camera %s: cannot listen on port %s: %s", self.name, self.config.port, exc)
            self._set_online(False)
            return
        self._thread = threading.Thread(target=self.server.serve_forever, name="smart-camera", daemon=True)
        self._thread.start()
        self._set_online(True)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self._set_online(False)

    def handle_push(self, data: dict[str, Any]) -> PlatePass:
        items = data.get("reads") or [data]
        reads = [
            FrameRead(
                item.get("plate"),
                float(item.get("confidence", 0)),
                item.get("vehicle_type") or data.get("vehicle_type"),
                float(item.get("vehicle_confidence", 0.9)),
            )
            for item in items
        ]
        image = data.get("image")
        snapshot = base64.b64decode(image, validate=True) if image else None
        return self._emit_pass(vote(reads, self.threshold), snapshot)


def create_plate_source(config: CameraConfig, clock: Clock, engine: AnprEngine | None = None) -> PlateSource:
    if config.kind == "simulator":
        return SimulatorPlateSource(config, clock)
    if config.kind == "rtsp":
        return RtspPlateSource(config, clock, engine)
    if config.kind == "smart":
        return SmartCameraSource(config, clock)
    return ManualPlateSource(config, clock)
