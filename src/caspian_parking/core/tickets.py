"""Human-readable ticket numbers ``G-SSSSS-C`` (SPEC §2.3).

G = gate code, S = per-gate sequence (at least 5 digits, never reused), C = Luhn check digit
computed over the digits of G and S. The check digit catches every single-digit typo and
almost every swap of two neighbouring digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from caspian_parking.core.digits import normalize_input

MIN_SEQUENCE_DIGITS = 5
MAX_GATE_CODE = 9

_TICKET_RE = re.compile(r"^(\d)\s*[-‐–—_/ ]?\s*(\d{5,})\s*[-‐–—_/ ]?\s*(\d)$")


class TicketNumberError(ValueError):
    pass


def luhn_check_digit(digits: str) -> int:
    """Luhn (mod 10) check digit to append to ``digits``."""
    if not digits.isdigit() or not digits.isascii():
        raise TicketNumberError("digits only")
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:  # positions that will be doubled once the check digit is appended
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10


def luhn_valid(digits_with_check: str) -> bool:
    if len(digits_with_check) < 2 or not digits_with_check.isdigit():
        return False
    return luhn_check_digit(digits_with_check[:-1]) == int(digits_with_check[-1])


@dataclass(frozen=True, order=True)
class TicketNumber:
    gate: int
    sequence: int

    def __post_init__(self) -> None:
        if not 1 <= self.gate <= MAX_GATE_CODE:
            raise TicketNumberError("gate code must be 1-9")
        if self.sequence < 1:
            raise TicketNumberError("sequence must be positive")

    @property
    def sequence_text(self) -> str:
        return str(self.sequence).zfill(MIN_SEQUENCE_DIGITS)

    @property
    def check_digit(self) -> int:
        return luhn_check_digit(f"{self.gate}{self.sequence_text}")

    def __str__(self) -> str:
        return f"{self.gate}-{self.sequence_text}-{self.check_digit}"


def parse_ticket_number(text: str) -> TicketNumber:
    """Parse a typed ticket number (any digits, dashes optional). Raises on a bad check digit."""
    compact = normalize_input(text)
    match = _TICKET_RE.match(compact)
    if not match:
        raise TicketNumberError(f"not a ticket number: {text!r}")
    gate, sequence, check = match.groups()
    if luhn_check_digit(gate + sequence) != int(check):
        raise TicketNumberError("check digit mismatch")
    return TicketNumber(int(gate), int(sequence))
