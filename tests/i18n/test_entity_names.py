"""Every table has a Persian name for the audit-log screen (found missing by the Phase 11 QA pass)."""

from __future__ import annotations

import caspian_parking.data.models  # noqa: F401 - registers every table
from caspian_parking.data.base import Base
from caspian_parking.i18n import has_key


def test_every_table_has_a_persian_name():
    missing = [name for name in Base.metadata.tables if not has_key(f"entity.{name}")]
    assert missing == []
