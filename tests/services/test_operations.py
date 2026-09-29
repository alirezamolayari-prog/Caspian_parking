"""Phase 6: backup/restore, fiscal year, photos, heartbeat/outages, maintenance, alerts, training."""

from __future__ import annotations

import os
import zipfile
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.models import FiscalYear, OutageEvent, Photo, Visit
from caspian_parking.data.session import ClosedYearError
from caspian_parking.services import auth, backup, fiscal, heartbeat, maintenance, photos
from caspian_parking.services.alerts import AlertMonitor, printer_alerts
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.context import open_context
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.settings import set_setting

MON = date(2026, 9, 28)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def ctx(app_ctx, clock):
    clock.set(at(MON, 10))
    with app_ctx.uow() as session:
        user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx


def one_visit(ctx, clock, plate="12ب345-22"):
    gate = GateService(ctx)
    entry = gate.register_entry(parse_plate(plate))
    clock.advance(minutes=30)
    return gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)


# ---------------------------------------------------------------- backup


def test_backup_restore_roundtrip(ctx, clock, tmp_path):
    one_visit(ctx, clock)
    (ctx.data_root.receipt / "logo.png").write_bytes(b"logo-v1")
    ctx.config.backup_destinations = [str(tmp_path / "usb")]
    results = backup.create_backup(ctx)
    assert len(results) == 2
    assert not backup.overdue(ctx)
    archive = results[0].path
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert backup.DB_ENTRY in names
    assert "files/receipt/logo.png" in names
    check = backup.test_restore(archive)
    assert check.ok
    assert check.visits == 1
    # change data after the backup, then restore
    clock.advance(minutes=5)
    one_visit(ctx, clock, "55ج777-11")
    (ctx.data_root.receipt / "logo.png").write_bytes(b"logo-v2")
    safety = backup.restore_backup(ctx, archive)
    assert safety.is_file()
    restored = open_context(ctx.data_root.root, clock=clock)
    try:
        with restored.read() as session:
            assert session.scalar(select(func.count()).select_from(Visit)) == 1
    finally:
        restored.close()
    assert (ctx.data_root.receipt / "logo.png").read_bytes() == b"logo-v1"


def test_retention_and_overdue(ctx, clock):
    with ctx.uow() as session:
        set_setting(session, "backup.keep", 2)
    assert backup.overdue(ctx)
    for _ in range(4):
        backup.create_backup(ctx)
        clock.advance(seconds=2)
    assert len(backup.list_backups(ctx.data_root.backups)) == 2
    clock.advance(hours=25)
    assert backup.overdue(ctx)


def test_unreachable_destination_does_not_break_backup(ctx):
    ctx.config.backup_destinations = ["Q:/not-a-drive/backups"]
    assert len(backup.create_backup(ctx)) == 1


def test_bad_archive(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(backup.BackupError):
        backup.test_restore(bad)


def test_restore_needs_permission(ctx, tmp_path):
    archive = backup.create_backup(ctx)[0].path
    ctx.user = None
    with pytest.raises(backup.BackupError, match="permission"):
        backup.restore_backup(ctx, archive)


@pytest.mark.mssql
def test_sql_server_backup_and_verify(mssql_engine_session, tmp_path):
    database = mssql_engine_session.url.query.get("odbc_connect", "")
    name = str(database).split("DATABASE=")[1].split(";")[0]
    path = backup.mssql_backup(mssql_engine_session, name, tmp_path / "server.bak")
    assert path.is_file()
    assert backup.mssql_verify(mssql_engine_session, path)
    maintenance.run_mssql_maintenance(mssql_engine_session)


# ---------------------------------------------------------------- fiscal year


def test_fiscal_year_close_archives_counters_and_locks(ctx, clock):
    with ctx.read() as session:
        current = fiscal.open_year(session)
    assert current.name == "1405"
    one_visit(ctx, clock)
    with pytest.raises(fiscal.FiscalError, match="confirm"):
        fiscal.close_year(ctx, "1404", "x")
    new_year = fiscal.close_year(ctx, "1405", "پایان سال")
    assert new_year.name == "1406"
    assert new_year.start_date == date(2027, 3, 21)
    with ctx.read() as session:
        closed = session.get(FiscalYear, current.id)
        assert closed.status == "closed"
        assert closed.final_counters["total"] == 1
    # counters restart in the new year; the old year is read-only
    clock.set(at(date(2027, 3, 22), 10))
    assert GateService(ctx).counters()["total"] == 0
    clock.set(at(MON, 12))  # PC clock wrongly set back into the closed year
    with pytest.raises(ClosedYearError):
        GateService(ctx).register_entry(parse_plate("31د456-44"))


def test_fiscal_needs_permission(ctx):
    ctx.user = None
    with pytest.raises(fiscal.FiscalError, match="permission"):
        fiscal.close_year(ctx, "1405", "x")


# ---------------------------------------------------------------- photos


def test_photo_file_name_matches_spec():
    moment = local_to_utc(datetime(2026, 9, 28, 14, 32, 10))
    name = photos.photo_filename(moment, "in", "درب یافت‌آباد", "12ب345-22", "subscriber", "علی رضایی", "پژو 206")
    assert name == "1405-07-06_14-32-10_ورود_درب\u200cیافت‌آباد_12ب345-22_مشترک_علی\u200cرضایی_پژو\u200c206.jpg"
    assert photos.sanitize('a/b:c*"d') == "a-b-c--d"
    assert "بدون‌پلاک" in photos.photo_filename(moment, "out", "درب", None, "transient")


def test_photo_storage_retention_and_protection(ctx, clock):
    old = clock.now_utc()
    kept = photos.store_photo(ctx, b"x" * 1000, old, "entry", "a.jpg")
    protected = photos.store_photo(ctx, b"x", old, "block", "b.jpg")
    forever = photos.store_photo(ctx, b"x", old, "entry", "c.jpg", keep_forever=True)
    duplicate = photos.store_photo(ctx, b"x", old, "entry", "a.jpg")
    assert duplicate.path != kept.path
    assert "1405" in kept.path
    clock.advance(days=200)
    removed = photos.cleanup(ctx)
    assert {p.name for p in removed} == {"a.jpg", "a_1.jpg"}
    assert os.path.exists(protected.path)
    assert os.path.exists(forever.path)
    with ctx.read() as session:
        assert session.get(Photo, kept.id).file_deleted_at_utc is not None
    stats = photos.usage(ctx)
    assert stats.free_bytes > 0
    assert 0 < stats.free_percent <= 100


def test_fled_session_photos_are_kept(ctx, clock):
    gate = GateService(ctx)
    entry = gate.register_entry(parse_plate("12ب345-22"))
    photo = photos.store_photo(ctx, b"x", clock.now_utc(), "entry", "fled.jpg", session_id=entry.session.id)
    clock.advance(minutes=30)
    gate.flee(gate.quote(entry.session.id))
    clock.advance(days=400)
    photos.cleanup(ctx)
    assert os.path.exists(photo.path)


# ---------------------------------------------------------------- heartbeat & outages


def test_outage_detection(ctx, clock):
    assert heartbeat.detect_outage(ctx) is None  # first start
    clock.advance(seconds=30)
    heartbeat.beat(ctx)
    clock.advance(minutes=45)  # power cut
    event = heartbeat.detect_outage(ctx)
    assert event is not None
    assert not event.clean_shutdown
    heartbeat.mark_clean_shutdown(ctx)
    clock.advance(hours=12)
    assert heartbeat.detect_outage(ctx).clean_shutdown
    clock.advance(seconds=40)
    assert heartbeat.detect_outage(ctx) is None
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(OutageEvent)) == 2
    from caspian_parking.services.reports import run_report
    from caspian_parking.services.reports.base import range_params

    result = run_report(ctx, "outages", range_params(MON, MON + timedelta(days=2)))
    assert result.totals["count"] == 2


# ---------------------------------------------------------------- maintenance & alerts


def test_maintenance_monthly(ctx, clock):
    assert maintenance.is_due(ctx)
    assert maintenance.run_maintenance(ctx)
    assert not maintenance.run_maintenance(ctx)
    clock.advance(days=31)
    assert maintenance.is_due(ctx)


def test_alert_monitor(ctx):
    class Status:
        online = False
        paper_ok = True

    monitor = AlertMonitor(ctx, [lambda: printer_alerts(Status())])
    current, cleared = monitor.evaluate()
    assert {a.key for a in current} >= {"backup", "printer"}
    backup.create_backup(ctx)
    Status.online = True
    current, cleared = monitor.evaluate()
    assert set(cleared) == {"backup", "printer"}
    Status.paper_ok = False
    assert printer_alerts(Status())[0].text_key == "printer.paper_out"


def test_training_mode_is_isolated(tmp_path, clock):
    real = open_context(tmp_path / "d", clock=clock)
    try:
        with real.uow() as session:
            user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
        real.user = CurrentUser.from_user(user)
        one_visit(real, clock)
    finally:
        real.close()
    training = open_context(tmp_path / "d", clock=clock, training=True)
    try:
        with training.read() as session:
            assert session.scalar(select(func.count()).select_from(Visit)) == 0
    finally:
        training.close()


def test_shutdown_tasks_backup_and_clean_flag(ctx, clock):
    import json

    from caspian_parking.app import shutdown_tasks

    shutdown_tasks(ctx)
    assert len(backup.list_backups(ctx.data_root.backups)) == 1
    state = json.loads((ctx.data_root.config / heartbeat.HEARTBEAT_FILE).read_text(encoding="utf-8"))
    assert state["clean"] is True
    with ctx.uow() as session:
        set_setting(session, "backup.on_close", False)
    clock.advance(seconds=5)
    shutdown_tasks(ctx)
    assert len(backup.list_backups(ctx.data_root.backups)) == 1
