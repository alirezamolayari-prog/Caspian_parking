"""Every report on a fixed dataset built through the real gate / people services."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from docx import Document
from openpyxl import load_workbook

from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.tariff import VehicleType, VisitKind
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.blocklist import BlocklistService
from caspian_parking.services.gate_service import Blocked, GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.reports import (
    REPORTS,
    ReportError,
    available_reports,
    export_report,
    get_report,
    run_report,
)
from caspian_parking.services.reports.base import day_params, month_params, range_params, this_month_params

MON = date(2026, 9, 28)  # 1405/07/06


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def dataset(app_ctx, clock):
    """Monday 1405/07/06 at gate 1 (all amounts per SPEC §4.5 defaults)."""
    with app_ctx.uow() as session:
        user = auth.create_user(session, "boss", "مدیر", "secret1", preset="admin")
    app_ctx.user = CurrentUser.from_user(user)
    gate = GateService(app_ctx)
    people = PeopleService(app_ctx)

    def visit(plate: str | None, minutes: int, method: PaymentMethod | None, **kwargs):
        entry = gate.register_entry(parse_plate(plate) if plate else None, **kwargs)
        clock.advance(minutes=minutes)
        quote = gate.quote(entry.session.id)
        return entry, quote, gate.complete_exit(quote, method) if quote.amount_due or method is None else None

    clock.set(at(MON, 10))
    visit("11ب111-11", 60, PaymentMethod.CASH)  # 190,000 cash
    visit("12ب111-11", 73, PaymentMethod.CARD)  # 240,000 card
    visit("13ب111-11", 120, PaymentMethod.MALL_CARD)  # 380,000 mall card
    visit("123-45678", 30, PaymentMethod.CASH, vehicle=VehicleType.MOTORCYCLE)  # 200,000
    visit(None, 10, PaymentMethod.CASH)  # no plate 190,000
    visit("14ب111-11", 15, None, kind=VisitKind.PASS_THROUGH, pass_type="taxi")  # free pass-through
    fled = gate.register_entry(parse_plate("15ب111-11"))
    clock.advance(minutes=60)
    debt = gate.flee(gate.quote(fled.session.id))  # 190,000 lost
    gate.collect_debt(debt.id, PaymentMethod.CASH, amount=90_000)  # partly recovered
    cancelled = gate.register_entry(parse_plate("16ب111-11"))
    gate.cancel_entry(cancelled.session.id, "چاپ اشتباه")
    lost = gate.register_entry(parse_plate("17ب111-11"))
    gate.reprint_duplicate(lost.session.id)
    clock.advance(minutes=30)
    gate.complete_exit(
        gate.quote(lost.session.id, lost_ticket=True), PaymentMethod.CASH, manual_amount=100_000, reason="تخفیف"
    )
    # a subscriber and a guard (free access)
    sub = people.create_person(PersonInput(first_name="علی", plates=[(parse_plate("21ب222-22"), None)]))
    people.pay_subscription(sub.id, "card")  # 4,000,000 card (subscription)
    gate.register_entry(parse_plate("21ب222-22"))
    staff = people.create_person(
        PersonInput(first_name="نگهبان", kind="free", free_category="staff", plates=[(parse_plate("22ب222-22"), None)])
    )
    gate.register_entry(parse_plate("22ب222-22"))
    # an overnight transient with its fine waived partly
    night_entry = gate.register_entry(parse_plate("18ب111-11"))
    BlocklistService(app_ctx).block_plate(parse_plate("19ب111-11"), "debtor", "بدهی")
    with pytest.raises(Blocked):
        gate.register_entry(parse_plate("19ب111-11"))
    clock.set(at(MON + timedelta(days=1), 10))
    night_quote = gate.quote(night_entry.session.id)
    gate.complete_exit(night_quote, PaymentMethod.CARD, night_fines=1_000_000, reason="تخفیف شب")
    return {"ctx": app_ctx, "gate": gate, "sub": sub, "staff": staff}


def run(dataset, key, params=None, **kwargs):
    params = params or range_params(MON, MON + timedelta(days=1), **kwargs)
    return run_report(dataset["ctx"], key, params)


def test_every_report_runs_and_has_a_title(dataset):
    params = range_params(MON, MON + timedelta(days=1), text="11ب111-11")
    for report in REPORTS:
        result = run_report(dataset["ctx"], report.key, params)
        assert result.title
        assert result.columns
        assert result.to_table().title == result.title


def test_financial(dataset):
    result = run(dataset, "financial", day_params(MON))
    # parking: cash 190k + 200k + 190k + 100k = 680k, card 240k, mall card 380k; debt cash 90k; subs card 4M
    assert result.totals["cash"] == 680_000 + 90_000
    assert result.totals["card"] == 240_000 + 4_000_000
    assert result.totals["mall_card"] == 380_000
    assert result.totals["grand"] == 5_390_000
    next_day = run(dataset, "financial", day_params(MON + timedelta(days=1)))
    # overnight: 18ب entered ~12:37 (after the day's visits) — card, fee + reduced night fine
    assert next_day.totals["card"] > 1_000_000
    assert run(dataset, "financial", day_params(MON, gate_code=1)).totals["grand"] == 5_390_000
    assert run(dataset, "financial", day_params(MON, gate_code=2)).totals["grand"] == 0


def test_debts(dataset):
    result = run(dataset, "debts")
    assert result.totals == {"count": 1, "lost": 190_000, "recovered": 90_000}


def test_night_and_manual_changes(dataset):
    night = run(dataset, "night", range_params(MON, MON + timedelta(days=1)))
    assert night.totals["visits"] == 1
    assert night.totals["fines"] == 2_000_000
    assert night.totals["waivers"] == 1
    manual = run(dataset, "manual_changes")
    assert manual.totals["count"] == 2  # manual amount + night fine
    assert manual.totals["difference"] < 0


def test_traffic_reports(dataset):
    assert run(dataset, "motorcycles").totals["count"] == 1
    assert run(dataset, "no_plate").totals["count"] == 1
    assert run(dataset, "pass_through").totals["count"] == 1
    assert run(dataset, "cancellations").totals["count"] == 1
    lost = run(dataset, "lost")
    assert lost.totals == {"lost": 1, "duplicates": 1}
    assert run(dataset, "block_attempts").totals["count"] == 1
    assert run(dataset, "free_access").totals["count"] == 1
    traffic = run(dataset, "subscriber_traffic")
    assert traffic.totals == {"people": 1, "entries": 1}


def test_subscriptions_report(dataset):
    result = run(dataset, "subscriptions", this_month_params(at(MON, 12)))
    assert result.totals["collected"] == 4_000_000
    assert result.totals["people"] == 1
    assert result.totals["expired"] == 0


def test_plate_history_and_frequent_visitors(dataset, clock):
    gate = dataset["gate"]
    for day in range(2, 7):
        clock.set(at(MON + timedelta(days=day), 11))
        entry = gate.register_entry(parse_plate("31د456-44"))
        clock.advance(minutes=20)
        gate.complete_exit(gate.quote(entry.session.id), PaymentMethod.CASH)
    week = range_params(MON, MON + timedelta(days=7), text="31د456-44")
    result = run_report(dataset["ctx"], "plate_history", week)
    assert result.totals["visits"] == 5
    assert result.totals["frequent"] == 1
    assert result.totals["paid"] == 3 * 190_000  # Thursday and Friday are free


def test_open_sessions(dataset, clock):
    result = run(dataset, "open_sessions", day_params(MON + timedelta(days=1)))
    assert result.totals["inside"] == 2  # subscriber + guard still inside
    assert result.totals["stale"] == 2


def test_occupancy_heatmap(dataset):
    result = run(dataset, "occupancy", day_params(MON))
    assert result.chart is not None
    assert len(result.chart["matrix"]) == 7
    assert len(result.chart["matrix"][0]) == 24
    assert result.totals["peak"] >= 2
    assert result.totals["capacity"] == 202


def test_executive_summary(dataset):
    result = run(dataset, "executive", month_params(1405, 7))
    assert result.totals["now_entries"] >= 12
    assert result.totals["now_revenue"] > 5_000_000
    assert result.totals["prev_entries"] == 0
    assert len(result.sections[0].rows) == 7


def test_exports_all_formats(dataset, tmp_path, qapp):
    result = run(dataset, "financial", day_params(MON))
    excel = export_report(result, tmp_path, "excel")
    word = export_report(result, tmp_path, "word")
    pdf = export_report(result, tmp_path, "pdf")
    assert load_workbook(excel).active.sheet_view.rightToLeft
    assert Document(str(word)).tables
    assert pdf.read_bytes().startswith(b"%PDF")
    with pytest.raises(ReportError):
        export_report(result, tmp_path, "csv")


def test_permissions(dataset, app_ctx):
    with app_ctx.uow() as session:
        operator = auth.create_user(session, "op", "op", "secret1", preset="operator")
    app_ctx.user = CurrentUser.from_user(operator)
    keys = {r.key for r in available_reports(app_ctx)}
    assert "financial" not in keys
    assert "occupancy" in keys
    with pytest.raises(ReportError, match="permission"):
        run_report(app_ctx, "financial", day_params(MON))
    with pytest.raises(ReportError):
        get_report("nope")


def test_params_labels():
    assert "۱۴۰۵/۰۷/۰۶" in day_params(MON).label
    assert "تا" in range_params(MON, MON + timedelta(days=3)).label
