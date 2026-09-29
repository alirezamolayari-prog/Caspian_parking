"""UUIDv7 identifiers (RFC 9562), time-ordered and safe to create on any node.

IDs are handled as canonical lowercase strings (36 chars) everywhere, so they sort by
creation time both in Python and in the database.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime

_lock = threading.Lock()
_last_ms = 0
_counter = 0
_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


def _next_timestamp_and_counter() -> tuple[int, int]:
    """Return (unix_ms, 12-bit counter); the counter keeps IDs monotonic within one millisecond."""
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # random start, room to grow
        else:
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1  # borrow the next millisecond rather than lose ordering
                _counter = 0
        return _last_ms, _counter


def uuid7() -> str:
    """Return a new UUIDv7 as a canonical string."""
    unix_ms, counter = _next_timestamp_and_counter()
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def uuid7_datetime(value: str) -> datetime:
    """Creation time (UTC) embedded in a UUIDv7 string."""
    parsed = uuid.UUID(value)
    if parsed.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    unix_ms = parsed.int >> 80
    return datetime.fromtimestamp(unix_ms / 1000, tz=UTC)


def is_uuid7(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 7 and str(parsed) == value
