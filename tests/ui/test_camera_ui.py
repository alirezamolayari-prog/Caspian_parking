"""Phase 8 UI: simulator cameras drive entry and exit end-to-end, corrections, unidentified passes, alerts."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from caspian_parking.config.machine import CameraConfig, load_machine_config
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.models import Photo, PlateCorrection, ReadMatch
from caspian_parking.devices.printer import SimulatorPrinter
from caspian_parking.services.gate_service import PaymentMethod
from caspian_parking.ui.receipt.printing import ReceiptPrinting
from caspian_parking.ui.screens.gate import GateScreen

MON = date(2026, 9, 28)
PLATE = "12ب345-22"


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def cam_ctx(admin_ctx, clock):
    clock.set(at(MON, 10))
    admin_ctx.config.cameras = [
        CameraConfig(lane="entry", name="ورودی ۱", kind="simulator"),
        CameraConfig(lane="exit", name="خروجی ۱", kind="simulator"),
    ]
    return admin_ctx


@pytest.fixture
def screen(qtbot, cam_ctx):
    widget = GateScreen(cam_ctx, ReceiptPrinting(cam_ctx, SimulatorPrinter()))
    qtbot.addWidget(widget)
    widget.resize(1600, 950)
    widget.show()
    widget.on_show()
    return widget


def test_camera_tiles_and_entry_from_camera(screen, cam_ctx):
    assert set(screen.tiles) == {"entry", "exit"}
    assert screen.tiles["entry"].online
    item = screen.sources["entry"].simulate(PLATE, vehicle_type="van")
    assert screen.plate_input.plate() == parse_plate(PLATE)
    assert screen.vehicle.value == "van"
    assert screen.tiles["entry"].plate.plate() == parse_plate(PLATE)
    assert item.result.accepted
    entry = screen.print_entry()  # one action prints the receipt
    assert entry is not None
    assert entry.session.vehicle_type == "van"
    with cam_ctx.read() as session:
        match = session.scalars(select(ReadMatch)).one()
        assert match.session_id == entry.session.id
        photo = session.scalars(select(Photo)).one()
        assert photo.session_id == entry.session.id


def test_operator_correction_is_stored(screen, cam_ctx):
    screen.sources["entry"].simulate("12ب346-22")
    assert screen.plate_input.set_text(PLATE)  # operator fixes one digit
    entry = screen.print_entry()
    with cam_ctx.read() as session:
        correction = session.scalars(select(PlateCorrection)).one()
    assert correction.read_plate_key == "12ب346-22"
    assert correction.corrected_plate_key == parse_plate(PLATE).key
    assert correction.session_id == entry.session.id


def test_exit_camera_loads_session_and_links_on_payment(screen, cam_ctx, clock):
    screen.sources["entry"].simulate(PLATE)
    entry = screen.print_entry()
    clock.advance(minutes=45)
    screen.sources["exit"].simulate(PLATE)
    assert screen.quote is not None
    assert screen.quote.session.id == entry.session.id
    assert screen.pay(PaymentMethod.CASH)
    with cam_ctx.read() as session:
        lanes = sorted(m.lane for m in session.scalars(select(ReadMatch)))
    assert lanes == ["entry", "exit"]


def test_motorcycle_read_switches_plate_mode(screen):
    screen.sources["entry"].simulate("123-45678", vehicle_type="motorcycle")
    assert screen.plate_input.plate() == parse_plate("123-45678")
    assert screen.current_vehicle().value == "motorcycle"


def test_unidentified_pass_listed_and_filled(screen, cam_ctx):
    screen.sources["entry"].simulate(None)
    assert screen.unidentified_model.loaded_count() == 1
    assert screen.unidentified_empty.isHidden()
    screen.unidentified_table.selectRow(0)
    screen._show_unidentified_photo()
    assert not screen.unidentified_photo.isHidden()
    screen.unidentified_plate.setText("55ج777-11")
    assert screen.fill_unidentified()
    assert screen.unidentified_model.loaded_count() == 0
    with cam_ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(PlateCorrection)) == 1


def test_camera_offline_raises_alert(qtbot, cam_ctx):
    from caspian_parking.app import build_main_window

    window = build_main_window(cam_ctx)
    qtbot.addWidget(window)
    gate = window.show_screen("gate")
    gate.sources["exit"].set_online(False)
    assert any(a.key == "camera_exit" for a in window.alerts.alerts())
    assert not gate.tiles["exit"].online
    gate.sources["exit"].set_online(True)
    assert not any(a.key == "camera_exit" for a in window.alerts.alerts())
    gate.stop_cameras()


def test_without_cameras_no_tiles(qtbot, admin_ctx):
    widget = GateScreen(admin_ctx, ReceiptPrinting(admin_ctx, SimulatorPrinter()))
    qtbot.addWidget(widget)
    assert widget.tiles == {}
    assert widget.sources == {}


def test_camera_settings_saved(qtbot, admin_ctx):
    from caspian_parking.ui.screens.settings_devices import HardwareTab

    tab = HardwareTab(admin_ctx)
    qtbot.addWidget(tab)
    row = tab.camera_rows["exit"]
    row["kind"].setCurrentIndex(row["kind"].findData("rtsp"))
    row["url"].setText("rtsp://10.0.0.9/stream1")
    row["threshold"].setValue(75)
    tab.save()
    config = load_machine_config(admin_ctx.data_root.config)
    exit_cam = config.camera_for("exit")
    assert (exit_cam.kind, exit_cam.url, exit_cam.min_confidence) == ("rtsp", "rtsp://10.0.0.9/stream1", 75)
    assert config.camera_for("entry") is None
