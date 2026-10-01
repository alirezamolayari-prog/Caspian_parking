"""Phase 8: camera reads stored with photos, matched to sessions, corrections, unidentified passes, report 17."""

from __future__ import annotations

import os
from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from caspian_parking.config.machine import CameraConfig
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.models import CameraRead, Photo, PlateCorrection, ReadMatch
from caspian_parking.devices.plate_source import SimulatorPlateSource
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.camera import CameraError, CameraService
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.reports import run_report
from caspian_parking.services.reports.base import range_params

MON = date(2026, 9, 28)
PLATE = "12ب345-22"


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def ctx(app_ctx, clock):
    clock.set(at(MON, 10))
    with app_ctx.uow() as session:
        user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx


@pytest.fixture
def entry_cam(qapp, ctx, clock):
    return SimulatorPlateSource(CameraConfig(lane="entry", name="ورودی", kind="simulator"), clock, seed=1)


@pytest.fixture
def exit_cam(qapp, ctx, clock):
    return SimulatorPlateSource(CameraConfig(lane="exit", name="خروجی", kind="simulator"), clock, seed=2)


def test_read_with_photo_and_entry_match(ctx, entry_cam):
    cameras = CameraService(ctx)
    PeopleService(ctx).create_person(
        PersonInput(first_name="علی", last_name="رضایی", vehicle_model="پژو 206", plates=[(parse_plate(PLATE), None)])
    )
    read = cameras.record_pass(entry_cam.simulate(PLATE))
    assert read.plate_key == parse_plate(PLATE).key
    assert read.accepted
    assert read.confidence == 92
    path = cameras.photo_path(read)
    assert path is not None
    assert os.path.exists(path)
    assert "مشترک" in os.path.basename(path)
    assert "پژو" in os.path.basename(path)
    entry = GateService(ctx).register_entry(parse_plate(PLATE))
    cameras.link(read.id, entry.session.id, parse_plate(PLATE), "sedan")
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(ReadMatch)) == 1
        assert session.scalar(select(func.count()).select_from(PlateCorrection)) == 0
        assert session.get(Photo, read.photo_id).session_id == entry.session.id


def test_operator_correction_keeps_both_values(ctx, entry_cam):
    cameras = CameraService(ctx)
    read = cameras.record_pass(entry_cam.simulate("12ب346-22"))
    fixed = parse_plate(PLATE)
    entry = GateService(ctx).register_entry(fixed)
    cameras.link(read.id, entry.session.id, fixed, "van")
    with ctx.read() as session:
        correction = session.scalars(select(PlateCorrection)).one()
    assert correction.read_plate_key == "12ب346-22"
    assert correction.corrected_plate_key == fixed.key
    assert correction.corrected_vehicle_type == "van"
    with pytest.raises(CameraError):
        cameras.link("missing", entry.session.id, fixed)


def test_unidentified_pass_list_and_fill(ctx, entry_cam, clock):
    cameras = CameraService(ctx)
    read = cameras.record_pass(entry_cam.simulate(None))
    assert read.plate_key is None
    assert [r.id for r in cameras.unidentified()] == [read.id]
    cameras.fill_unidentified(read.id, parse_plate(PLATE))
    assert cameras.unidentified() == []
    old = cameras.record_pass(entry_cam.simulate(None))
    clock.advance(days=8)
    assert old.id not in [r.id for r in cameras.unidentified()]


def test_exit_read_match_and_accuracy_report(ctx, entry_cam, exit_cam, clock):
    cameras = CameraService(ctx)
    gate = GateService(ctx)
    good = cameras.record_pass(entry_cam.simulate(PLATE))
    entry = gate.register_entry(parse_plate(PLATE))
    cameras.link(good.id, entry.session.id, parse_plate(PLATE))
    wrong = cameras.record_pass(entry_cam.simulate("55ج778-11"))
    other = gate.register_entry(parse_plate("55ج777-11"))
    cameras.link(wrong.id, other.session.id, parse_plate("55ج777-11"))
    cameras.record_pass(entry_cam.simulate(None))
    clock.advance(minutes=40)
    out = cameras.record_pass(exit_cam.simulate(PLATE))
    gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)
    cameras.link(out.id, entry.session.id, parse_plate(PLATE))
    result = run_report(ctx, "camera_accuracy", range_params(MON, MON))
    assert result.totals == {"reads": 4, "matched": 3, "corrected": 1, "unidentified": 1}
    summary = {row[0]: row for row in result.sections[0].rows}
    assert summary["ورودی"][2:6] == [3, 2, 1, 1]
    assert summary["خروجی"][2:6] == [1, 1, 0, 0]
    assert len(result.sections[1].rows) == 1  # the corrected read
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(CameraRead)) == 4
