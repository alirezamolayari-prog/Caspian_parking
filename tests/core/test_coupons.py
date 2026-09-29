from __future__ import annotations

import random

from caspian_parking.core.barcode import code128c_modules
from caspian_parking.core.coupons import (
    COUPON_LENGTH,
    format_coupon_code,
    is_coupon_code,
    new_coupon_code,
    normalize_coupon_code,
)


def test_new_codes_are_valid_and_distinct():
    codes = {new_coupon_code() for _ in range(2000)}
    assert len(codes) == 2000
    for code in codes:
        assert len(code) == COUPON_LENGTH
        assert code.startswith("9")
        assert is_coupon_code(code)
        assert code128c_modules(code)  # even length → Code 128 C


def test_deterministic_generator():
    rng = random.Random(7)
    assert new_coupon_code(rng.randrange) == new_coupon_code(random.Random(7).randrange)


def test_every_single_digit_error_is_caught():
    code = new_coupon_code()
    for index in range(1, COUPON_LENGTH):
        for digit in "0123456789":
            if digit == code[index]:
                continue
            assert not is_coupon_code(code[:index] + digit + code[index + 1 :])


def test_rejects_ticket_payloads_and_garbage():
    assert not is_coupon_code("12345678901234567890")
    assert not is_coupon_code("")
    assert not is_coupon_code("abc")
    body = "1" + new_coupon_code()[1:]
    assert not is_coupon_code(body)  # wrong prefix


def test_normalize_and_format():
    code = new_coupon_code()
    persian = code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert normalize_coupon_code(f" {persian[:4]}-{persian[4:]} ") == code
    assert is_coupon_code(format_coupon_code(code))
    assert format_coupon_code("912345678901") == "9123-4567-8901"
