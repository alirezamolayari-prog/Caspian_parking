"""Phase 9: after-hours watch mode, morning report acknowledgement, report 18, server host, auto-update,
review queue, subscription payment cancellation."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from caspian_parking.config.machine import CameraConfig
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.models import CameraRead, MorningAck, WalletTransaction
from caspian_parking.devices.plate_source import SimulatorPlateSource
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.blocklist import BlocklistService
from caspian_parking.services.camera import CameraService
from caspian_parking.services.people import PeopleError, PeopleService, PersonInput
from caspian_parking.services.reports import run_report
from caspian_parking.services.reports.base import range_params
from caspian_parking.services.server_host import ServerHost
from caspian_parking.services.updater import apply_update, check_for_update, install_command, parse_version
from caspian_parking.services.watch import WatchService, is_working, night_window

MON = date(2026, 9, 28)
TUE = MON + timedelta(days=1)
PLATE = parse_plate("12ب345-22")


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def ctx(app_ctx, clock):
    clock.set(at(MON, 10))
    with app_ctx.uow() as session:
        user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx


def test_working_hours_and_night_window(ctx):
    with ctx.read() as session:
        assert is_working(session, at(MON, 10))
        assert not is_working(session, at(MON, 21))
        assert not is_working(session, at(TUE, 6))
        start, end = night_window(session, TUE)
    assert (start, end) == (at(MON, 20, 30), at(TUE, 9, 30))


def test_night_passes_morning_ack_and_report(qapp, ctx, clock):
    BlocklistService(ctx).block_plate(PLATE, "security", "تست")
    camera = SimulatorPlateSource(CameraConfig(lane="entry", kind="simulator"), clock)
    cameras, watch = CameraService(ctx), WatchService(ctx)
    clock.set(at(MON, 23))
    assert watch.after_hours_now()
    cameras.record_pass(camera.simulate(PLATE.key), after_hours=True)
    clock.set(at(TUE, 3))
    cameras.record_pass(camera.simulate("55ج777-11"), after_hours=True)
    clock.set(at(TUE, 8))
    assert watch.pending_report() is None  # the night is not over yet
    clock.set(at(TUE, 9, 45))
    report = watch.pending_report()
    assert report is not None
    assert len(report.passes) == 2
    assert report.blocked_count == 1
    ack = watch.acknowledge(report)
    assert ack.created_by == ctx.user.id  # who and when are kept
    assert watch.pending_report() is None  # only the first user of the day sees it
    with ctx.read() as session:
        assert session.scalar(select(MorningAck)).passes == 2
    result = run_report(ctx, "after_hours", range_params(MON, TUE))
    assert result.totals["passes"] == 2
    assert result.totals["blocked"] == 1


def test_server_host_watch_mode(qapp, ctx, clock):
    ctx.config.cameras = [CameraConfig(lane="entry", name="سرور", kind="simulator")]
    host = ServerHost(ctx)
    host.start()
    try:
        assert host.scheduler is not None
        source = host.sources[0]
        clock.set(at(MON, 22))
        source.simulate(PLATE.key)
        clock.set(at(TUE, 11))
        source.simulate(PLATE.key)
        assert host.passes == 2
        with ctx.read() as session:
            flags = [r.after_hours for r in session.scalars(select(CameraRead).order_by(CameraRead.created_at_utc))]
        assert flags == [True, False]
    finally:
        host.stop()
    assert host.sources == []


def test_updater(tmp_path):
    assert parse_version("1.10.2") > parse_version("1.9.9")
    assert check_for_update(tmp_path / "missing", "1.0.0") is None
    (tmp_path / "version.json").write_text(json.dumps({"version": "2.0.0", "installer": "setup.exe"}), "utf-8")
    assert check_for_update(tmp_path, "1.0.0") is None  # installer file not there yet
    (tmp_path / "setup.exe").write_bytes(b"MZ")
    info = check_for_update(tmp_path, "1.0.0")
    assert info is not None
    assert info.version == "2.0.0"
    assert check_for_update(tmp_path, "2.0.0") is None
    launched = []
    apply_update(info, launched.append)
    assert launched == [install_command(info)]
    assert "/VERYSILENT" in launched[0]


def test_update_at_start_only_on_gates(ctx, tmp_path):
    from caspian_parking.app import update_at_start
    from caspian_parking.config.machine import Role
    from caspian_parking.services.settings import set_setting

    (tmp_path / "version.json").write_text(json.dumps({"version": "99.0.0", "installer": "setup.exe"}), "utf-8")
    (tmp_path / "setup.exe").write_bytes(b"MZ")
    with ctx.uow() as session:
        set_setting(session, "update.share", str(tmp_path))
    assert not update_at_start(ctx)  # standalone PCs are updated by hand
    ctx.config.role = Role.GATE
    from caspian_parking.services import updater

    calls = []
    original = updater.apply_update
    updater.apply_update = lambda info, launcher=None: calls.append(info.version)  # type: ignore[assignment]
    try:
        assert update_at_start(ctx)
    finally:
        updater.apply_update = original
    assert calls == ["99.0.0"]


def test_cancel_subscription_payment(ctx):
    people = PeopleService(ctx)
    shop = people.create_shop("مغازه")
    people.deposit(shop.id, 10_000_000, "cash")
    person = people.create_person(PersonInput(first_name="علی", shop_id=shop.id, payer="shop", plates=[(PLATE, None)]))
    payment = people.pay_subscription(person.id, "wallet")
    people.cancel_subscription_payment(payment.id, "اشتباه")
    with ctx.read() as session:
        from caspian_parking.data.models import Person

        assert session.get(Person, person.id).subscription_end_utc is None
        refund = session.scalar(select(WalletTransaction).where(WalletTransaction.kind == "adjustment"))
        assert refund.amount == payment.amount
    assert people.balance(shop.id) == 10_000_000
    with pytest.raises(PeopleError, match="already_cancelled"):
        people.cancel_subscription_payment(payment.id, "دوباره")
    with pytest.raises(PeopleError, match="reason"):
        people.cancel_subscription_payment(payment.id, " ")
