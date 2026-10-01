"""LED sign (SPEC §4.11, §6): rotating text (free spaces, ads).

Generic drivers send one line of UTF-8 text (``\\r\\n`` terminated) over a COM port or TCP. Many local LED
controllers use their own protocol and fonts; such a controller gets its own driver class here.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from typing import Protocol

from caspian_parking.config.machine import DeviceConfig

log = logging.getLogger(__name__)


class LedError(RuntimeError):
    pass


class LedSign(Protocol):
    def show(self, text: str) -> None: ...


@dataclass
class SimulatorLed:
    shown: list[str] = field(default_factory=list)

    def show(self, text: str) -> None:
        self.shown.append(text)


class SerialLed:
    def __init__(self, port: str, baud: int) -> None:
        self.port, self.baud = port, baud

    def show(self, text: str) -> None:
        import serial

        try:
            with serial.Serial(self.port, self.baud, timeout=1) as device:
                device.write(text.encode("utf-8") + b"\r\n")
        except serial.SerialException as exc:
            raise LedError(str(exc)) from exc


class TcpLed:
    def __init__(self, address: str) -> None:
        host, _sep, port = address.rpartition(":")
        if not host or not port.isdigit():
            raise LedError("led.bad_address")
        self.host, self.port = host, int(port)

    def show(self, text: str) -> None:
        with socket.create_connection((self.host, self.port), timeout=3) as conn:
            conn.sendall(text.encode("utf-8") + b"\r\n")


def create_led(config: DeviceConfig) -> LedSign | None:
    if config.led_kind == "simulator":
        return SimulatorLed()
    if config.led_kind == "serial" and config.led_port:
        return SerialLed(config.led_port, config.led_baud)
    if config.led_kind == "tcp" and config.led_port:
        return TcpLed(config.led_port)
    return None
