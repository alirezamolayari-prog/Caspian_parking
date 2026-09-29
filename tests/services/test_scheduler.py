from __future__ import annotations

from datetime import date, datetime, time, timedelta

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.gate_service import GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.scheduler import AppScheduler, catch_up, due_days, generate_daily_report, renew_wallets
from caspian_parking.services.settings import get_setting, set_setting

MON = date(2026, 9, 28)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


def _login(ctx):
    with ctx.uow() as session:
        user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
    ctx.user = CurrentUser.from_user(user)


def test_daily_report_files(app_ctx, clock):
    _login(app_ctx)
    clock.set(at(MON, 10))
    gate = GateService(app_ctx)
    entry = gate.register_entry(parse_plate("12ب345-22"))
    clock.advance(minutes=30)
    gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)
    app_ctx.user = None  # the scheduler runs without a user
    files = generate_daily_report(app_ctx, MON)
    assert len(files) == 8  # 4 reports × Excel + Word
    assert all(f.is_file() for f in files)
    assert files[0].parent == app_ctx.data_root.reports / "1405" / "07"
    with app_ctx.read() as session:
        assert get_setting(session, "reports.daily_last") == MON.isoformat()


def test_due_days_and_catch_up(app_ctx, clock):
    clock.set(at(MON, 20))  # before 21:00 → yesterday is the latest due day
    days = due_days(app_ctx, clock.now_utc())
    assert days[-1] == MON - timedelta(days=1)
    assert len(days) == 7  # never more than a week of catch-up
    clock.set(at(MON, 21, 5))
    with app_ctx.uow() as session:
        set_setting(session, "reports.daily_last", (MON - timedelta(days=2)).isoformat())
    assert due_days(app_ctx, clock.now_utc()) == [MON - timedelta(days=1), MON]
    done = catch_up(app_ctx)
    assert done == [MON - timedelta(days=1), MON]
    assert due_days(app_ctx, clock.now_utc()) == []
    with app_ctx.uow() as session:
        set_setting(session, "reports.daily_enabled", False)
    assert due_days(app_ctx, clock.now_utc() + timedelta(days=3)) == []


def test_custom_folder(app_ctx, clock, tmp_path):
    with app_ctx.uow() as session:
        set_setting(session, "reports.daily_folder", str(tmp_path / "owner"))
        set_setting(session, "reports.daily_formats", ["excel"])
    files = generate_daily_report(app_ctx, MON)
    assert len(files) == 4
    assert str(files[0]).startswith(str(tmp_path / "owner"))


def test_wallet_renewal_job(app_ctx, clock):
    _login(app_ctx)
    service = PeopleService(app_ctx)
    shop = service.create_shop("مغازه")
    service.create_person(
        PersonInput(first_name="الف", shop_id=shop.id, payer="shop", plates=[(parse_plate("12ب345-22"), None)])
    )
    service.deposit(shop.id, 5_000_000, "cash")
    app_ctx.user = None
    assert renew_wallets(app_ctx) == 1
    with app_ctx.uow() as session:
        set_setting(session, "wallet.auto_renew", False)
    assert renew_wallets(app_ctx) == 0


def test_scheduler_registers_jobs(app_ctx):
    scheduler = AppScheduler(app_ctx)
    scheduler.start()
    try:
        assert {"daily_report", "wallet_renewal"} <= set(scheduler.job_ids())
    finally:
        scheduler.stop()
