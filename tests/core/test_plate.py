from __future__ import annotations

import pytest

from caspian_parking.core.plate import (
    LETTERS,
    PlateKind,
    car_plate,
    motorcycle_plate,
    parse_plate,
    plate_from_key,
)


@pytest.mark.parametrize(
    "text",
    [
        "12ب345-22",
        "12 ب 345 ایران 22",
        "۱۲ب۳۴۵۲۲",
        "١٢ ب ٣٤٥ | ٢٢",
        "12-ب-345-22",
    ],
)
def test_car_plate_variants_normalize_to_same_key(text):
    plate = parse_plate(text)
    assert plate.kind is PlateKind.CAR
    assert plate.key == "12ب345-22"
    assert plate.region == "22"
    assert plate.letter == "ب"


def test_alef_and_latin_letters():
    assert parse_plate("12الف345 11").key == "12الف345-11"
    assert parse_plate("12 d 345 11").key == "12D345-11"


def test_arabic_keyboard_letters_are_fixed():
    assert parse_plate("12ي345-22").key == "12ی345-22"


def test_motorcycle_plate():
    plate = parse_plate("123 45678")
    assert plate.kind is PlateKind.MOTORCYCLE
    assert plate.key == "M123-45678"
    assert plate.text == "123-45678"


def test_free_plate_and_strict_mode():
    plate = parse_plate("TEMP 99")
    assert plate.kind is PlateKind.FREE
    assert plate.key == "F:TEMP99"
    with pytest.raises(ValueError, match="not an Iranian plate"):
        parse_plate("TEMP 99", allow_free=False)
    with pytest.raises(ValueError, match="empty"):
        parse_plate("  -  ")


@pytest.mark.parametrize("letter", LETTERS)
def test_every_letter_roundtrips(letter):
    plate = car_plate("12", letter, "345", "22")
    assert parse_plate(plate.key) == plate
    assert plate_from_key(plate.key) == plate


def test_builders_validate():
    with pytest.raises(ValueError, match="letter"):
        car_plate("12", "X", "345", "22")
    with pytest.raises(ValueError, match="2 \\+ 3"):
        car_plate("1", "ب", "345", "22")
    with pytest.raises(ValueError, match="region"):
        car_plate("12", "ب", "345", "2")
    with pytest.raises(ValueError, match="3 \\+ 5"):
        motorcycle_plate("12", "45678")
    assert car_plate("۱۲", "ب", "۳۴۵", "۲۲").key == "12ب345-22"


def test_keys_roundtrip_all_kinds():
    for text in ("12ب345-22", "123-45678", "ABC"):
        plate = parse_plate(text)
        assert plate_from_key(plate.key) == plate
