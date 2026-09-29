"""Iranian license plates: parsing, normalization and canonical keys.

Car plate layout (left → right on the physical plate): ``12 ب 345 | ایران 22``.
Motorcycle plate: 3-digit city code over a 5-digit number.
Anything else (temporary, foreign, unreadable) is kept as a free-form plate.
The ``key`` is the indexed search value stored in the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from caspian_parking.core.digits import normalize_input

LETTERS: tuple[str, ...] = (
    "الف",
    "ب",
    "پ",
    "ت",
    "ث",
    "ج",
    "د",
    "ز",
    "س",
    "ش",
    "ص",
    "ط",
    "ع",
    "ق",
    "ک",
    "گ",
    "ل",
    "م",
    "ن",
    "و",
    "ه",
    "ی",
    "ژ",
    "D",
    "S",
)

_IRAN_WORD = "ایران"
_SEPARATORS = re.compile(r"[\s\-_|/\\.,:ـ]+")
_LETTER_ALT = "|".join(sorted((re.escape(x) for x in LETTERS), key=len, reverse=True))
_CAR_RE = re.compile(rf"^(\d{{2}})({_LETTER_ALT})(\d{{3}})(\d{{2}})$")
_MOTO_RE = re.compile(r"^(\d{3})(\d{5})$")


class PlateKind(StrEnum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    FREE = "free"


@dataclass(frozen=True)
class Plate:
    kind: PlateKind
    parts: tuple[str, ...]

    @property
    def key(self) -> str:
        """Canonical search key (Latin digits, no spaces)."""
        if self.kind is PlateKind.CAR:
            left, letter, mid, region = self.parts
            return f"{left}{letter}{mid}-{region}"
        if self.kind is PlateKind.MOTORCYCLE:
            top, bottom = self.parts
            return f"M{top}-{bottom}"
        return "F:" + self.parts[0]

    @property
    def text(self) -> str:
        """Compact human form used in file names and exports (Latin digits)."""
        if self.kind is PlateKind.FREE:
            return self.parts[0]
        return self.key.removeprefix("M")

    @property
    def region(self) -> str | None:
        return self.parts[3] if self.kind is PlateKind.CAR else None

    @property
    def letter(self) -> str | None:
        return self.parts[1] if self.kind is PlateKind.CAR else None

    def __str__(self) -> str:
        return self.key


def _compact(text: str) -> str:
    cleaned = normalize_input(text).replace(_IRAN_WORD, "").replace("IRAN", "").replace("I.R.", "")
    return _SEPARATORS.sub("", cleaned).upper() if cleaned.isascii() else _SEPARATORS.sub("", cleaned)


def parse_plate(text: str, allow_free: bool = True) -> Plate:
    """Parse typed/camera text into a Plate. Raises ValueError if empty or unparseable and not free."""
    compact = _compact(text)
    if not compact:
        raise ValueError("empty plate")
    if match := _CAR_RE.match(compact):
        return Plate(PlateKind.CAR, tuple(match.groups()))
    if match := _MOTO_RE.match(compact):
        return Plate(PlateKind.MOTORCYCLE, tuple(match.groups()))
    if allow_free:
        return Plate(PlateKind.FREE, (compact,))
    raise ValueError(f"not an Iranian plate: {text!r}")


def car_plate(left: str, letter: str, mid: str, region: str) -> Plate:
    """Build a car plate from its four fields (validated)."""
    left, mid, region = (normalize_input(x) for x in (left, mid, region))
    if letter not in LETTERS:
        raise ValueError(f"unknown plate letter: {letter!r}")
    if not (len(left) == 2 and left.isdigit() and len(mid) == 3 and mid.isdigit()):
        raise ValueError("car plate needs 2 + 3 digits")
    if not (len(region) == 2 and region.isdigit()):
        raise ValueError("region code needs 2 digits")
    return Plate(PlateKind.CAR, (left, letter, mid, region))


def motorcycle_plate(top: str, bottom: str) -> Plate:
    top, bottom = normalize_input(top), normalize_input(bottom)
    if not (len(top) == 3 and top.isdigit() and len(bottom) == 5 and bottom.isdigit()):
        raise ValueError("motorcycle plate needs 3 + 5 digits")
    return Plate(PlateKind.MOTORCYCLE, (top, bottom))


def plate_from_key(key: str) -> Plate:
    """Inverse of ``Plate.key``."""
    if key.startswith("F:"):
        return Plate(PlateKind.FREE, (key[2:],))
    if key.startswith("M"):
        top, bottom = key[1:].split("-", 1)
        return motorcycle_plate(top, bottom)
    return parse_plate(key, allow_free=False)
