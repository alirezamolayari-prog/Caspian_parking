from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from caspian_parking.core.ids import is_uuid7, uuid7, uuid7_datetime


def test_uuid7_format_and_version():
    value = uuid7()
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert len(value) == 36
    assert value == value.lower()
    assert is_uuid7(value)


def test_uuid7_is_unique_and_monotonic():
    values = [uuid7() for _ in range(20_000)]
    assert len(set(values)) == len(values)
    assert values == sorted(values)


def test_uuid7_embeds_creation_time():
    before = datetime.now(UTC) - timedelta(seconds=1)
    created = uuid7_datetime(uuid7())
    assert before <= created <= datetime.now(UTC) + timedelta(seconds=1)


def test_is_uuid7_rejects_other_values():
    assert not is_uuid7(str(uuid.uuid4()))
    assert not is_uuid7("not-a-uuid")
    assert not is_uuid7(uuid7().upper())
