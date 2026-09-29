"""Bidirectional-text helpers.

Every left-to-right run embedded in Persian text (numbers with separators, times, ticket
numbers, codes, English words, paths) is wrapped in Unicode isolates so it cannot reorder
the surrounding right-to-left text.
"""

from __future__ import annotations

LRI = chr(0x2066)  # left-to-right isolate
RLI = chr(0x2067)  # right-to-left isolate
FSI = chr(0x2068)  # first-strong isolate
PDI = chr(0x2069)  # pop directional isolate
LRM = chr(0x200E)
RLM = chr(0x200F)

ISOLATE_CHARS = frozenset((LRI, RLI, FSI, PDI))


def ltr(text: object) -> str:
    """Isolate a left-to-right run (ticket numbers, times, amounts, Latin words)."""
    return f"{LRI}{text}{PDI}"


def rtl(text: object) -> str:
    """Isolate a right-to-left run inside left-to-right text."""
    return f"{RLI}{text}{PDI}"


def auto(text: object) -> str:
    """Isolate with direction taken from the first strong character."""
    return f"{FSI}{text}{PDI}"


def strip_isolates(text: str) -> str:
    return "".join(ch for ch in text if ch not in ISOLATE_CHARS and ch not in (LRM, RLM))


def is_balanced(text: str) -> bool:
    """True if every isolate opener has a matching PDI and none is closed early."""
    depth = 0
    for ch in text:
        if ch in (LRI, RLI, FSI):
            depth += 1
        elif ch == PDI:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
