"""Money is always an integer number of Rial. No floats anywhere in money paths."""

from __future__ import annotations

from caspian_parking.core.digits import PERSIAN_THOUSANDS_SEPARATOR, parse_int, to_persian_digits

Rial = int


def require_int(amount: object) -> int:
    """Reject anything that is not a plain int (bool and float included)."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError(f"money must be int Rial, got {type(amount).__name__}")
    return amount


def group_thousands(amount: int, separator: str = ",") -> str:
    require_int(amount)
    sign = "-" if amount < 0 else ""
    return sign + f"{abs(amount):,}".replace(",", separator)


def format_rial(amount: int, persian_digits: bool = True) -> str:
    """``190000`` → ``۱۹۰٬۰۰۰`` (or ``190,000`` with Latin digits)."""
    if persian_digits:
        return to_persian_digits(group_thousands(amount, PERSIAN_THOUSANDS_SEPARATOR))
    return group_thousands(amount, ",")


def parse_rial(text: str) -> int:
    """Parse a typed amount (any digits, optional separators) into int Rial."""
    return parse_int(text)


def ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division for non-negative denominators."""
    require_int(numerator)
    require_int(denominator)
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return -(-numerator // denominator)


def round_up_to_step(amount: int, step: int) -> int:
    """Round ``amount`` up to a multiple of ``step`` (step <= 1 means no rounding)."""
    require_int(amount)
    require_int(step)
    if step <= 1:
        return amount
    return ceil_div(amount, step) * step
