"""SPEC §2.4 performance targets on the 1,000,000-visit dataset (scripts/perf.ps1).

entry receipt < 1 s · plate search < 200 ms · exit price < 100 ms · main window < 3 s · month report < 5 s
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from caspian_parking.app import smoke_login
from caspian_parking.core.jalali import jalali_of
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.receipt import ReceiptLayout
from caspian_parking.data.models import Visit
from caspian_parking.services.context import open_context
from caspian_parking.services.gate_service import GateService
from caspian_parking.services.people import PeopleService
from caspian_parking.services.receipts import entry_content
from caspian_parking.services.reports import REPORTS, run_report
from caspian_parking.services.reports.base import month_params, previous_month

pytestmark = pytest.mark.perf

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("PARKING_BIGDATA", ROOT / ".bigdata" / "data"))
RESULTS = ROOT / "tests" / "artifacts" / "perf.json"
_results: dict[str, float] = {}


@pytest.fixture(scope="module")
def ctx():
    if not (DATA / "db" / "local.db").exists():
        pytest.skip("big dataset missing — run scripts/perf.ps1 (it seeds it first)")
    context = open_context(DATA)
    smoke_login(context)
    yield context
    context.close()
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(_results, indent=2), encoding="utf-8")


def timed(name: str, function, repeat: int = 3) -> float:
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        best = min(best, time.perf_counter() - started)
    _results[name] = round(best, 4)
    return best


def test_dataset_size(ctx):
    with ctx.read() as session:
        from sqlalchemy import func

        assert session.scalar(select(func.count()).select_from(Visit)) >= 1_000_000


def test_plate_search_under_200ms(ctx):
    with ctx.read() as session:
        key = session.scalar(select(Visit.plate_key).where(Visit.plate_key.is_not(None)).limit(1))
    gate = GateService(ctx)
    people = PeopleService(ctx)

    def search() -> None:
        with ctx.read() as session:
            session.scalars(select(Visit).where(Visit.plate_key == key).limit(200)).all()
        gate.search_inside(key[:5])
        people.search(key[:5])

    assert timed("plate_search_s", search) < 0.2


def test_entry_receipt_under_1s(ctx, qapp):
    from caspian_parking.ui.receipt.renderer import render_receipt

    gate = GateService(ctx)
    run = random.Random().randint(10, 99)  # fresh plates on every run (earlier ones are still inside)
    plates = iter(f"{run}ج{500 + i}-{10 + i}" for i in range(10))

    def entry() -> None:
        result = gate.register_entry(parse_plate(next(plates)))
        render_receipt(entry_content(result.session, result.payload), ReceiptLayout())

    assert timed("entry_receipt_s", entry) < 1.0


def test_exit_price_under_100ms(ctx):
    gate = GateService(ctx)
    session = gate.search_inside(limit=1)[0]
    assert timed("exit_price_s", lambda: gate.quote(session.id)) < 0.1


def test_every_month_report_under_5s(ctx):
    today = jalali_of(ctx.clock.now_utc())
    year, month = previous_month(today.year, today.month)
    params = month_params(year, month, text="12ب345-22")  # a full month (~27,000 visits)
    slowest = 0.0
    for report in REPORTS:
        seconds = timed(f"report_{report.key}_s", lambda k=report.key: run_report(ctx, k, params), repeat=1)
        slowest = max(slowest, seconds)
        assert seconds < 5.0, report.key
    _results["slowest_month_report_s"] = round(slowest, 3)


def test_main_window_under_3s(ctx):
    output = subprocess.run(
        [sys.executable, "-m", "caspian_parking", "--smoke", "--data-root", str(DATA)],
        capture_output=True,
        timeout=180,
        check=True,
    ).stdout.decode(errors="replace")
    seconds = float(output.split("ready in")[1].split("s")[0])
    _results["main_window_s"] = seconds
    assert seconds < 3.0
