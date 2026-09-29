from __future__ import annotations

import pytest

from caspian_parking.core.digits import (
    digits_only,
    normalize_input,
    parse_int,
    to_latin_digits,
    to_persian_digits,
)
from caspian_parking.core.money import (
    ceil_div,
    format_rial,
    group_thousands,
    parse_rial,
    require_int,
    round_up_to_step,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("۱۲۳۴۵۶۷۸۹۰", "1234567890"), ("١٢٣٤٥٦٧٨٩٠", "1234567890"), ("12۳٤5", "12345"), ("abc", "abc")],
)
def test_to_latin_digits(text, expected):
    assert to_latin_digits(text) == expected


def test_to_persian_digits():
    assert to_persian_digits("1405/07/06") == "۱۴۰۵/۰۷/۰۶"
    assert to_persian_digits("٤") == "۴"


def test_normalize_input_fixes_arabic_letters_and_marks():
    assert normalize_input("  علي ك ۱۲" + chr(0x200F) + " ") == "علی ک 12"


def test_digits_only():
    assert digits_only("1-24715-8") == "1247158"
    assert digits_only("۱-۲۴۷۱۵-۸") == "1247158"


@pytest.mark.parametrize(
    ("text", "value"),
    [("190,000", 190000), ("۱۹۰٬۰۰۰", 190000), ("١٩٠٠٠٠", 190000), ("-5", -5), (" 7 ", 7)],
)
def test_parse_int(text, value):
    assert parse_int(text) == value
    assert parse_rial(text) == value


@pytest.mark.parametrize("text", ["", "12a", "1.5", "--1", "+"])
def test_parse_int_rejects(text):
    with pytest.raises(ValueError, match="not an integer"):
        parse_int(text)


def test_format_rial():
    assert format_rial(190000) == "۱۹۰٬۰۰۰"
    assert format_rial(190000, persian_digits=False) == "190,000"
    assert format_rial(0) == "۰"
    assert format_rial(-2_000_000, persian_digits=False) == "-2,000,000"
    assert group_thousands(1234567, " ") == "1 234 567"


@pytest.mark.parametrize("bad", [1.0, True, "1", None])
def test_money_rejects_non_int(bad):
    with pytest.raises(TypeError):
        require_int(bad)


@pytest.mark.parametrize(
    ("amount", "step", "expected"),
    [
        (190000, 10000, 190000),
        (190001, 10000, 200000),
        (0, 10000, 0),
        (5, 1, 5),
        (5, 0, 5),
        (199999, 100000, 200000),
    ],
)
def test_round_up_to_step(amount, step, expected):
    assert round_up_to_step(amount, step) == expected


def test_ceil_div():
    assert ceil_div(190000 * 1, 60) == 3167
    assert ceil_div(120, 60) == 2
    assert ceil_div(0, 60) == 0
    with pytest.raises(ValueError, match="positive"):
        ceil_div(1, 0)
