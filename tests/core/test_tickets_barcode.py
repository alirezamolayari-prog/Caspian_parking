"""Ticket number (Luhn) and barcode payload/HMAC/Code 128 — exhaustive tests (SPEC §2.3)."""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caspian_parking.core.barcode import (
    CODE128_PATTERNS,
    PAYLOAD_LENGTH,
    BarcodeError,
    ForgedTicketError,
    code128_decode_modules,
    code128c_modules,
    code128c_values,
    decode_payload,
    encode_payload,
    entry_minute,
    looks_like_payload,
)
from caspian_parking.core.tickets import (
    TicketNumber,
    TicketNumberError,
    luhn_check_digit,
    luhn_valid,
    parse_ticket_number,
)

KEY = b"k" * 32
OTHER_KEY = b"x" * 32
ENTRY = datetime(2026, 9, 28, 10, 32, 41, tzinfo=UTC)

# ---------------------------------------------------------------- Luhn


@pytest.mark.parametrize(("digits", "check"), [("7992739871", 3), ("124715", 4), ("0", 0), ("18", 2)])
def test_luhn_known_values(digits, check):
    assert luhn_check_digit(digits) == check
    assert luhn_valid(digits + str(check))


def test_luhn_rejects_non_digits():
    with pytest.raises(TicketNumberError):
        luhn_check_digit("12a")
    assert not luhn_valid("1")
    assert not luhn_valid("1x")


def test_luhn_catches_every_single_digit_error():
    rng = random.Random(7)
    for _ in range(300):
        body = "".join(rng.choice("0123456789") for _ in range(rng.randint(6, 9)))
        full = body + str(luhn_check_digit(body))
        for position, wrong in itertools.product(range(len(full)), "0123456789"):
            if full[position] == wrong:
                continue
            typo = full[:position] + wrong + full[position + 1 :]
            assert not luhn_valid(typo), (full, typo)


def test_luhn_catches_adjacent_transpositions_except_09_90():
    rng = random.Random(11)
    for _ in range(300):
        body = "".join(rng.choice("0123456789") for _ in range(8))
        full = body + str(luhn_check_digit(body))
        for i in range(len(full) - 1):
            a, b = full[i], full[i + 1]
            if a == b or {a, b} == {"0", "9"}:
                continue
            swapped = full[:i] + b + a + full[i + 2 :]
            assert not luhn_valid(swapped), (full, swapped)


# ---------------------------------------------------------------- ticket numbers


def test_ticket_number_format_and_parse():
    ticket = TicketNumber(1, 24715)
    assert str(ticket) == "1-24715-4"
    assert parse_ticket_number("1-24715-4") == ticket
    assert parse_ticket_number("۱-۲۴۷۱۵-۴") == ticket
    assert parse_ticket_number("1247154") == ticket
    assert parse_ticket_number(" 1 24715 4 ") == ticket
    assert str(TicketNumber(2, 7)) == f"2-00007-{luhn_check_digit('200007')}"
    long = TicketNumber(2, 1_234_567)
    assert parse_ticket_number(str(long)) == long


@pytest.mark.parametrize("bad", ["1-24715-8", "1-2471-4", "0-24715-4", "abc", "", "1-24715"])
def test_ticket_number_rejects(bad):
    with pytest.raises(TicketNumberError):
        parse_ticket_number(bad)


def test_ticket_number_validation():
    with pytest.raises(TicketNumberError):
        TicketNumber(0, 1)
    with pytest.raises(TicketNumberError):
        TicketNumber(10, 1)
    with pytest.raises(TicketNumberError):
        TicketNumber(1, 0)


# ---------------------------------------------------------------- payload


def test_payload_roundtrip():
    payload = encode_payload(1, 24715, ENTRY, KEY)
    assert len(payload) == PAYLOAD_LENGTH
    assert payload.isdigit()
    decoded = decode_payload(payload, KEY)
    assert decoded.gate == 1
    assert decoded.sequence_mod == 24715
    assert decoded.entry_utc == ENTRY.replace(second=0)
    assert decoded.entry_minute == entry_minute(ENTRY)
    assert looks_like_payload(payload)
    assert not looks_like_payload("1-24715-4")


def test_payload_accepts_persian_digits_from_scanners():
    payload = encode_payload(2, 99, ENTRY, KEY)
    persian = payload.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert decode_payload(persian, KEY).sequence_mod == 99


def test_sequence_wraps_in_payload():
    assert decode_payload(encode_payload(3, 1_000_123, ENTRY, KEY), KEY).sequence_mod == 123


def test_every_single_digit_edit_is_rejected():
    payload = encode_payload(1, 24715, ENTRY, KEY)
    accepted = 0
    for position, digit in itertools.product(range(PAYLOAD_LENGTH), "0123456789"):
        if payload[position] == digit:
            continue
        edited = payload[:position] + digit + payload[position + 1 :]
        try:
            decode_payload(edited, KEY)
            accepted += 1
        except BarcodeError:
            pass
    assert accepted == 0


def test_forged_and_malformed_payloads():
    payload = encode_payload(1, 5, ENTRY, KEY)
    with pytest.raises(ForgedTicketError):
        decode_payload(payload, OTHER_KEY)
    with pytest.raises(BarcodeError, match="length"):
        decode_payload(payload[:-1], KEY)
    with pytest.raises(BarcodeError):
        encode_payload(0, 1, ENTRY, KEY)
    with pytest.raises(BarcodeError):
        encode_payload(1, 1, ENTRY, b"")
    with pytest.raises(BarcodeError, match="range"):
        encode_payload(1, 1, ENTRY + timedelta(days=365 * 200), KEY)


def test_forged_gate_zero_rejected():
    body = "0" + encode_payload(1, 5, ENTRY, KEY)[1:15]
    import hashlib
    import hmac as _hmac

    digest = _hmac.new(KEY, body.encode(), hashlib.sha256).digest()
    mac = str(int.from_bytes(digest[:8], "big") % 100000).zfill(5)
    with pytest.raises(BarcodeError, match="gate"):
        decode_payload(body + mac, KEY)


# ---------------------------------------------------------------- Code 128


def test_code128_table_is_consistent():
    assert len(CODE128_PATTERNS) == 107
    for value, pattern in enumerate(CODE128_PATTERNS):
        expected = 13 if value == 106 else 11
        assert sum(int(w) for w in pattern) == expected, value
    assert len(set(CODE128_PATTERNS)) == 107


def test_code128_known_checksum():
    # "123456" in set C → 105, 12, 34, 56, checksum (105 + 12·1 + 34·2 + 56·3) % 103 = 353 % 103 = 44, stop
    assert code128c_values("123456") == [105, 12, 34, 56, 44, 106]


def test_code128_rejects_odd_or_non_digits():
    for bad in ("123", "", "12a4"):
        with pytest.raises(BarcodeError):
            code128c_values(bad)


def test_code128_payload_width_fits_80mm_paper():
    modules = code128c_modules(encode_payload(1, 24715, ENTRY, KEY))
    assert len(modules) == 11 * 12 + 13  # start + 10 data + checksum + stop
    total_modules = len(modules) + 2 * 10  # quiet zones
    assert total_modules * 3 <= 576  # module ≥ 3 px on a 576 px printable width
    assert modules[0] is True
    assert modules[-1] is True


@given(st.text(alphabet="0123456789", min_size=2, max_size=40).filter(lambda s: len(s) % 2 == 0))
def test_code128_roundtrip(digits):
    assert code128_decode_modules(code128c_modules(digits)) == digits


def test_code128_decoder_detects_damage():
    modules = code128c_modules("12345678")
    damaged = modules[:30] + [not m for m in modules[30:33]] + modules[33:]
    with pytest.raises(BarcodeError):
        code128_decode_modules(damaged)
    with pytest.raises(BarcodeError):
        code128_decode_modules(modules[:22])
