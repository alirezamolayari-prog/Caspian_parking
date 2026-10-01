"""Barrier relay (SPEC §6): opens the arm for a short pulse.

* ``SimulatorBarrier`` – records opens (tests, training, PCs without a barrier),
* ``SerialRelayBarrier`` – USB/serial relay boards: "open" bytes, wait ``pulse_ms``, "close" bytes,
* ``TcpRelayBarrier`` – network relay boards that take the same bytes over TCP,
* ``HttpRelayBarrier`` – relay boards with a web API (GET to a URL).

Every open is sent on a worker thread so the gate screen never waits for the hardware.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from caspian_parking.config.machine import DeviceConfig

log = logging.getLogger(__name__)

TCP_TIMEOUT_S = 3


class BarrierError(RuntimeError):
    pass


def parse_hex(text: str) -> bytes:
    """ "A0 01 01 A2" / "A00101A2" / "0xA0,0x01" → bytes."""
    cleaned = text.replace("0x", "").replace("0X", "").replace(",", " ").replace(" ", "")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise BarrierError("barrier.bad_command") from exc


class Barrier(Protocol):
    def open(self, lane: str) -> None: ...

    def test(self) -> bool: ...


@dataclass
class SimulatorBarrier:
    opened: list[str] = field(default_factory=list)
    online: bool = True

    def open(self, lane: str) -> None:
        if not self.online:
            raise BarrierError("barrier.offline")
        self.opened.append(lane)

    def test(self) -> bool:
        return self.online


class _PulseBarrier:
    """Common part: send "open", wait, send "close" — on a worker thread."""

    def __init__(self, config: DeviceConfig, runner: Callable[[Callable[[], None]], Any] | None = None) -> None:
        self.config = config
        self.open_cmd = parse_hex(config.barrier_open_cmd)
        self.close_cmd = parse_hex(config.barrier_close_cmd) if config.barrier_close_cmd.strip() else b""
        self.runner = runner or (lambda job: threading.Thread(target=job, daemon=True).start())

    def _send(self, payload: bytes) -> None:
        raise NotImplementedError

    def _pulse(self) -> None:
        try:
            self._send(self.open_cmd)
            if self.close_cmd:
                time.sleep(max(0, self.config.barrier_pulse_ms) / 1000)
                self._send(self.close_cmd)
        except (OSError, BarrierError) as exc:
            log.error("barrier pulse failed: %s", exc)

    def open(self, lane: str) -> None:
        self.runner(self._pulse)

    def test(self) -> bool:
        try:
            self._send(b"")
        except (OSError, BarrierError):
            return False
        return True


class SerialRelayBarrier(_PulseBarrier):
    def _send(self, payload: bytes) -> None:
        import serial

        try:
            with serial.Serial(self.config.barrier_port, self.config.barrier_baud, timeout=1) as port:
                if payload:
                    port.write(payload)
        except serial.SerialException as exc:
            raise BarrierError(str(exc)) from exc


class TcpRelayBarrier(_PulseBarrier):
    def _send(self, payload: bytes) -> None:
        host, _sep, port = self.config.barrier_port.rpartition(":")
        if not host or not port.isdigit():
            raise BarrierError("barrier.bad_address")
        with socket.create_connection((host, int(port)), timeout=TCP_TIMEOUT_S) as conn:
            if payload:
                conn.sendall(payload)


class HttpRelayBarrier(_PulseBarrier):
    """``barrier_port`` = URL that opens the relay (e.g. http://10.0.0.50/relay?on=1); no close needed."""

    def __init__(self, config: DeviceConfig, runner: Callable[[Callable[[], None]], Any] | None = None) -> None:
        self.config = config
        self.open_cmd = b""
        self.close_cmd = b""
        self.runner = runner or (lambda job: threading.Thread(target=job, daemon=True).start())

    def _send(self, payload: bytes) -> None:
        with urllib.request.urlopen(self.config.barrier_port, timeout=TCP_TIMEOUT_S) as response:
            response.read()

    def _pulse(self) -> None:
        try:
            self._send(b"")
        except OSError as exc:
            log.error("barrier request failed: %s", exc)


def create_barrier(config: DeviceConfig) -> Barrier | None:
    kind = config.barrier_kind
    if kind == "simulator":
        return SimulatorBarrier()
    if kind == "serial":
        return SerialRelayBarrier(config)
    if kind == "tcp":
        return TcpRelayBarrier(config)
    if kind == "http":
        return HttpRelayBarrier(config)
    return None
