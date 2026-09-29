from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from caspian_parking.i18n import has_key, tr
from caspian_parking.i18n.bidi import FSI, LRI, PDI, RLI, auto, is_balanced, ltr, rtl, strip_isolates
from caspian_parking.i18n.format import (
    fa_date,
    fa_duration,
    fa_money,
    fa_time,
    jalali_month_name,
    weekday_name,
)

MOMENT = datetime(2026, 9, 28, 10, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "caspian_parking"


def test_tr_and_placeholders():
    assert tr("money.unit") == "ریال"
    assert tr("duration.minutes", m="۵") == "۵ دقیقه"
    assert tr("no.such.key") == "no.such.key"
    assert tr("duration.minutes", wrong="x") == "{m} دقیقه"


def test_catalog_is_valid_json_and_has_no_empty_values():
    text = resources.files("caspian_parking.i18n").joinpath("fa.json").read_text("utf-8")
    data = json.loads(text)
    assert all(isinstance(v, str) and v for k, v in data.items() if not k.startswith("_"))


def test_every_tr_key_used_in_code_exists():
    pattern = re.compile(r"""\btr\(\s*["']([a-z0-9_.]+)["']""")
    missing = set()
    for path in SRC.rglob("*.py"):
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            if not has_key(key):
                missing.add(f"{path.name}: {key}")
    assert not missing, sorted(missing)


def test_isolates():
    assert ltr("1-24715-8") == f"{LRI}1-24715-8{PDI}"
    assert rtl("سلام") == f"{RLI}سلام{PDI}"
    assert auto("x") == f"{FSI}x{PDI}"
    assert strip_isolates(ltr("abc") + "\u200f") == "abc"


def test_mixed_direction_strings_are_balanced():
    samples = [
        f"رسید {ltr('1-24715-8')} صادر شد",
        f"مبلغ {fa_money(190000)} پرداخت شد",
        f"ورود {fa_date(MOMENT)} ساعت {fa_time(MOMENT)}",
        f"فایل {ltr('C:/CaspianParking/exports/a.xlsx')}",
        ltr(f"x {rtl('ب')} y"),
    ]
    for text in samples:
        assert is_balanced(text), repr(text)
    assert not is_balanced(f"{PDI}abc{LRI}")
    assert not is_balanced(f"{LRI}abc")


def test_display_formats_use_persian_digits():
    moment = datetime(2026, 9, 28, 11, 2, tzinfo=UTC)
    assert strip_isolates(fa_date(moment)) == "۱۴۰۵/۰۷/۰۶"
    assert strip_isolates(fa_time(moment)) == "۱۴:۳۲"
    assert strip_isolates(fa_money(2_000_000)) == "۲٬۰۰۰٬۰۰۰ ریال"
    assert fa_duration(75) == "۱ ساعت و ۱۵ دقیقه"
    assert fa_duration(5) == "۵ دقیقه"
    assert weekday_name(4) == "جمعه"
    assert jalali_month_name(1) == "فروردین"
