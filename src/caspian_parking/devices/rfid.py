"""RFID / UHF card readers (SPEC §6). Every reader emits ``card_read(uid)`` with a normalised card number.

* ``SimulatorReader`` – ``present("0012345678")``,
* keyboard-wedge (HID) readers type the number like a barcode scanner: the gate's scanner filter
  already catches them, and the gate checks scanned text against registered cards,
* ``SerialReader`` – one line per card on a COM port,
* ``TcpReader`` – UHF long-range readers that send one line per card over TCP (auto-reconnect).
"""

from __future__ import annotations

import logging
import socket
import threading
import time

from PySide6.QtCore import QObject, Signal

from caspian_parking.config.machine import DeviceConfig
from caspian_parking.core.digits import normalize_input

log = logging.getLogger(__name__)

RECONNECT_S = 5.0
REPEAT_BLOCK_S = 3.0  # UHF readers repeat a tag many times per second while the car is in range


def normalize_card(text: str) -> str:
    """Card numbers are compared without spaces/dashes, upper-case hex, Latin digits."""
    return "".join(ch for ch in normalize_input(text).upper() if ch.isalnum())


class CardReader(QObject):
    card_read = Signal(str)
    online_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._last: tuple[str, float] | None = None
        self.online = False

    def _emit(self, raw: str) -> None:
        uid = normalize_card(raw)
        if not uid:
            return
        now = time.monotonic()
        if self._last is not None and self._last[0] == uid and now - self._last[1] < REPEAT_BLOCK_S:
            return
        self._last = (uid, now)
        self.card_read.emit(uid)

    def _set_online(self, online: bool) -> None:
        if online != self.online:
            self.online = online
            self.online_changed.emit(online)

    def start(self) -> None:
        self._set_online(True)

    def stop(self) -> None:
        self._set_online(False)


class SimulatorReader(CardReader):
    def present(self, uid: str) -> None:
        self._emit(uid)


class _LineReader(CardReader):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rfid", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._set_online(False)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_lines()
            except OSError as exc:
                log.warning("card reader: %s", exc)
            self._set_online(False)
            self._stop.wait(RECONNECT_S)

    def _read_lines(self) -> None:
        raise NotImplementedError


class SerialReader(_LineReader):
    def __init__(self, port: str, baud: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port, self.baud = port, baud

    def _read_lines(self) -> None:
        import serial

        try:
            with serial.Serial(self.port, self.baud, timeout=0.5) as device:
                self._set_online(True)
                buffer = b""
                while not self._stop.is_set():
                    buffer += device.read(64)
                    while b"\n" in buffer or b"\r" in buffer:
                        line, buffer = _split_line(buffer)
                        self._emit(line.decode("ascii", "ignore"))
        except serial.SerialException as exc:
            raise OSError(str(exc)) from exc


class TcpReader(_LineReader):
    def __init__(self, address: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        host, _sep, port = address.rpartition(":")
        self.host, self.port = host, int(port) if port.isdigit() else 0

    def _read_lines(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5) as conn:
            conn.settimeout(0.5)
            self._set_online(True)
            buffer = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(256)
                except TimeoutError:
                    continue
                if not chunk:
                    raise OSError("reader closed the connection")
                buffer += chunk
                while b"\n" in buffer or b"\r" in buffer:
                    line, buffer = _split_line(buffer)
                    self._emit(line.decode("ascii", "ignore"))


def _split_line(buffer: bytes) -> tuple[bytes, bytes]:
    positions = [p for p in (buffer.find(b"\n"), buffer.find(b"\r")) if p >= 0]
    end = min(positions)
    return buffer[:end], buffer[end + 1 :]


def create_reader(config: DeviceConfig) -> CardReader | None:
    """``wedge`` returns None: the gate's keyboard-wedge scanner filter already delivers those cards."""
    if config.rfid_kind == "simulator":
        return SimulatorReader()
    if config.rfid_kind == "serial" and config.rfid_port:
        return SerialReader(config.rfid_port, config.rfid_baud)
    if config.rfid_kind == "tcp" and config.rfid_port:
        return TcpReader(config.rfid_port)
    return None
