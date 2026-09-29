"""Coupon codes: 12 digits = prefix '9' + 10 random digits + Luhn check digit.

The prefix keeps coupons apart from ticket payloads (20 digits) when the same scanner reads both;
12 digits encode as 6 Code 128 C symbols, so the barcode is short and easy to scan.
"""

from __future__ import annotations

import secrets

from caspian_parking.core.digits import digits_only
from caspian_parking.core.tickets import luhn_check_digit, luhn_valid

COUPON_PREFIX = "9"
COUPON_LENGTH = 12
_RANDOM_DIGITS = COUPON_LENGTH - len(COUPON_PREFIX) - 1


def new_coupon_code(randbelow=secrets.randbelow) -> str:  # type: ignore[no-untyped-def]
    body = COUPON_PREFIX + "".join(str(randbelow(10)) for _ in range(_RANDOM_DIGITS))
    return body + str(luhn_check_digit(body))


def normalize_coupon_code(text: str) -> str:
    """Scanner/typed input → bare digits (Persian/Arabic digits, spaces and dashes accepted)."""
    return digits_only(text)


def is_coupon_code(text: str) -> bool:
    code = normalize_coupon_code(text)
    return len(code) == COUPON_LENGTH and code.startswith(COUPON_PREFIX) and luhn_valid(code)


def format_coupon_code(code: str) -> str:
    """Grouped for printing: 9123-4567-8901."""
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))
