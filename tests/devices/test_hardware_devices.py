"""Barrier relays, card readers, card terminal simulator, LED sign drivers (network ones against local
TCP/HTTP servers)."""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from caspian_parking.config.machine import DeviceConfig
from caspian_parking.devices.barrier import (
    BarrierError,
    HttpRelayBarrier,
    SimulatorBarrier,
    TcpRelayBarrier,
    create_barrier,
    parse_hex,
)
from caspian_parking.devices.led import LedError, SimulatorLed, TcpLed, create_led
from caspian_parking.devices.payment import SimulatorTerminal, create_terminal
from caspian_parking.devices.rfid import SimulatorReader, TcpReader, create_reader, normalize_card


class _Collect(socketserver.BaseRequestHandler):
    received: ClassVar[list[bytes]] = []

    def handle(self) -> None:
        data = b""
        self.request.settimeout(2)
        try:
            while chunk := self.request.recv(1024):
                data += chunk
        except TimeoutError:
            pass
        type(self).received.append(data)


@pytest.fixture
def tcp_sink():
    handler = type("Sink", (_Collect,), {"received": []})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, handler
    server.shutdown()
    server.server_close()


def test_parse_hex():
    assert parse_hex("A0 01 01 A2") == bytes([0xA0, 1, 1, 0xA2])
    assert parse_hex("0xA0,0x01") == bytes([0xA0, 1])
    with pytest.raises(BarrierError):
        parse_hex("zz")


def test_tcp_relay_sends_open_then_close(tcp_sink):
    server, handler = tcp_sink
    config = DeviceConfig(barrier_kind="tcp", barrier_port=f"127.0.0.1:{server.server_address[1]}", barrier_pulse_ms=10)
    barrier = TcpRelayBarrier(config, runner=lambda job: job())  # synchronous for the test
    barrier.open("entry")
    deadline = time.monotonic() + 3
    while len([d for d in handler.received if d]) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    payloads = [d for d in handler.received if d]
    assert payloads == [parse_hex(config.barrier_open_cmd), parse_hex(config.barrier_close_cmd)]
    assert barrier.test()
    bad = TcpRelayBarrier(DeviceConfig(barrier_port="nohost"), runner=lambda job: job())
    assert not bad.test()


def test_http_relay():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/relay?on=1"
        HttpRelayBarrier(DeviceConfig(barrier_kind="http", barrier_port=url), runner=lambda job: job()).open("exit")
        assert calls == ["/relay?on=1"]
    finally:
        server.shutdown()
        server.server_close()


def test_simulator_barrier_and_factory():
    barrier = SimulatorBarrier()
    barrier.open("exit")
    assert barrier.opened == ["exit"]
    barrier.online = False
    with pytest.raises(BarrierError):
        barrier.open("exit")
    assert create_barrier(DeviceConfig()) is None
    assert isinstance(create_barrier(DeviceConfig(barrier_kind="simulator")), SimulatorBarrier)
    assert isinstance(create_barrier(DeviceConfig(barrier_kind="tcp", barrier_port="1.2.3.4:5")), TcpRelayBarrier)


def test_card_reader_simulator_debounces_repeats(qapp):
    reader = SimulatorReader()
    seen = []
    reader.card_read.connect(seen.append)
    reader.present("00 12-34 ab")
    reader.present("001234AB")  # UHF readers repeat the tag while the car is in range
    reader.present("99")
    assert seen == ["001234AB", "99"]
    assert normalize_card("۰۰۱۲") == "0012"
    assert create_reader(DeviceConfig()) is None
    assert create_reader(DeviceConfig(rfid_kind="wedge")) is None  # handled by the scanner filter
    assert isinstance(create_reader(DeviceConfig(rfid_kind="simulator")), SimulatorReader)


def test_tcp_uhf_reader(qapp, qtbot):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        conn.sendall(b"E200341201\r\nE200341202\n")
        time.sleep(1)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    reader = TcpReader(f"127.0.0.1:{port}")
    seen = []
    reader.card_read.connect(seen.append)
    reader.start()
    try:
        qtbot.waitUntil(lambda: len(seen) == 2, timeout=5000)
    finally:
        reader.stop()
        listener.close()
    assert seen == ["E200341201", "E200341202"]
    assert not reader.online


def test_terminal_simulator_outcomes():
    approve = SimulatorTerminal()
    result = approve.request(120_000, 30)
    assert result.approved
    assert result.trace and len(result.trace) == 12
    assert approve.requests == [120_000]
    for outcome, key in (("decline", "pos.declined"), ("timeout", "pos.timeout"), ("offline", "pos.offline")):
        failed = SimulatorTerminal(outcome).request(1, 30)
        assert not failed.approved
        assert failed.error == key
    assert create_terminal("manual") is None
    assert isinstance(create_terminal("simulator"), SimulatorTerminal)


def test_led_drivers(tcp_sink):
    server, handler = tcp_sink
    led = TcpLed(f"127.0.0.1:{server.server_address[1]}")
    led.show("طبقه ۱: ۱۲ جای خالی")
    deadline = time.monotonic() + 3
    while not any(handler.received) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "طبقه ۱: ۱۲ جای خالی".encode() + b"\r\n" in handler.received
    with pytest.raises(LedError):
        TcpLed("nohost")
    assert isinstance(create_led(DeviceConfig(led_kind="simulator")), SimulatorLed)
    assert create_led(DeviceConfig(led_kind="serial")) is None  # no port configured
