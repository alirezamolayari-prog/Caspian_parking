"""Digit and Persian-text normalization.

Every input accepts Persian, Arabic-Indic and Latin digits; storage always uses Latin digits;
the UI always displays Persian digits.
"""

from __future__ import annotations

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
LATIN_DIGITS = "0123456789"

_TO_LATIN = str.maketrans(PERSIAN_DIGITS + ARABIC_DIGITS, LATIN_DIGITS * 2)
_TO_PERSIAN = str.maketrans(LATIN_DIGITS + ARABIC_DIGITS, PERSIAN_DIGITS * 2)

# Arabic letter forms that are typed on Arabic keyboards / produced by scanners.
_LETTER_FIXES = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        chr(0x200E): None,  # LRM
        chr(0x200F): None,  # RLM
    }
)

PERSIAN_THOUSANDS_SEPARATOR = "٬"
PERSIAN_DECIMAL_SEPARATOR = "٫"


def to_latin_digits(text: str) -> str:
    return text.translate(_TO_LATIN)


def to_persian_digits(text: str) -> str:
    return text.translate(_TO_PERSIAN)


def normalize_input(text: str) -> str:
    """Normalize user/scanner input: Latin digits, Persian letter forms, trimmed."""
    return to_latin_digits(text.translate(_LETTER_FIXES)).strip()


def digits_only(text: str) -> str:
    """Keep only digits (after normalizing Persian/Arabic digits to Latin)."""
    return "".join(ch for ch in to_latin_digits(text) if ch in LATIN_DIGITS)


def parse_int(text: str) -> int:
    """Parse an integer typed with any digit set and optional separators (, ٬ space)."""
    cleaned = normalize_input(text)
    for sep in (",", PERSIAN_THOUSANDS_SEPARATOR, " ", "٫", "_"):
        cleaned = cleaned.replace(sep, "")
    if cleaned.startswith(("+", "-")):
        sign, body = cleaned[0], cleaned[1:]
    else:
        sign, body = "", cleaned
    if not body.isdigit() or not body.isascii():
        raise ValueError(f"not an integer: {text!r}")
    return int(sign + body)
