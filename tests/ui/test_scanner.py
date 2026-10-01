from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from caspian_parking.devices.scanner import SerialScanner, SimulatorScanner, WedgeScanner


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def key(char: str) -> QKeyEvent:
    code = Qt.Key.Key_0 + int(char) if char.isdigit() else Qt.Key.Key_A
    return QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier, char)


def enter() -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")


def test_fast_burst_with_enter_is_a_scan(qtbot, themed):
    clock = FakeClock()
    scanner = WedgeScanner(clock_ms=clock)
    field = QLineEdit()
    qtbot.addWidget(field)
    scans = []
    scanner.scanned.connect(scans.append)
    for char in "12345678901234567890":
        assert scanner.handle_key(field, key(char))
        clock.now += 8
    assert scanner.handle_key(field, enter())
    assert scans == ["12345678901234567890"]
    assert field.text() == ""  # nothing leaked into the focused field


def test_slow_typing_reaches_the_field(qtbot, themed):
    clock = FakeClock()
    scanner = WedgeScanner(clock_ms=clock)
    field = QLineEdit()
    qtbot.addWidget(field)
    scans = []
    scanner.scanned.connect(scans.append)
    for char in "123":
        scanner.handle_key(field, key(char))
        clock.now += 150  # human typing speed
    scanner.timeout()  # the hold timer fires for the last key
    assert field.text() == "123"
    assert not scanner.handle_key(field, enter())
    assert scans == []


def test_short_fast_burst_is_ignored(qtbot, themed):
    clock = FakeClock()
    scanner = WedgeScanner(clock_ms=clock, min_length=6)
    field = QLineEdit()
    qtbot.addWidget(field)
    scans = []
    scanner.scanned.connect(scans.append)
    for char in "123":
        scanner.handle_key(field, key(char))
        clock.now += 5
    scanner.handle_key(field, enter())
    assert scans == []


def test_burst_without_enter_ends_on_timeout(qtbot, themed):
    clock = FakeClock()
    scanner = WedgeScanner(clock_ms=clock)
    field = QLineEdit()
    qtbot.addWidget(field)
    scans = []
    scanner.scanned.connect(scans.append)
    for char in "۱۲۳۴۵۶۷۸":  # Persian digits (Persian keyboard layout) are normalized
        scanner.handle_key(field, key(char))
        clock.now += 5
    scanner.timeout()
    assert scans == ["12345678"]


def test_modifier_keys_pass_through(qtbot, themed):
    scanner = WedgeScanner(clock_ms=FakeClock())
    field = QLineEdit()
    qtbot.addWidget(field)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier, "k")
    assert not scanner.handle_key(field, event)


@pytest.mark.serial  # real key timing (35 ms bursts): runs alone, not under parallel load
def test_installed_filter_works_with_real_events(qtbot, themed):
    scanner = WedgeScanner()
    scanner.install()
    try:
        field = QLineEdit()
        qtbot.addWidget(field)
        field.show()
        field.setFocus()
        scans = []
        scanner.scanned.connect(scans.append)
        qtbot.keyClicks(field, "1234567890")  # QTest types instantly → looks like a scanner
        qtbot.keyClick(field, Qt.Key.Key_Return)
        assert scans == ["1234567890"]
        assert field.text() == ""
    finally:
        scanner.uninstall()


def test_simulator_and_serial_objects(qtbot, themed):
    simulator = SimulatorScanner()
    scans = []
    simulator.scanned.connect(scans.append)
    simulator.feed(" ۱-۲۴۷۱۵-۴ ")
    simulator.feed("   ")
    assert scans == ["1-24715-4"]
    serial = SerialScanner("COM_DOES_NOT_EXIST")
    assert not serial.connected
    serial.stop()
