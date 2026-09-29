"""Heartbeat every 30 s → outage log (SPEC §2.5).

At start the gap since the last heartbeat is measured; a gap longer than ``OUTAGE_AFTER`` is written
as an outage (from the last heartbeat to now), noting whether the app had shut down cleanly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from caspian_parking.data.models import OutageEvent
from caspian_parking.services.context import AppContext

HEARTBEAT_FILE = "heartbeat.json"
INTERVAL_SECONDS = 30
OUTAGE_AFTER = timedelta(seconds=90)


def _path(ctx: AppContext) -> Path:
    return ctx.data_root.config / HEARTBEAT_FILE


def _write(ctx: AppContext, clean: bool) -> None:
    path = _path(ctx)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last": ctx.clock.now_utc().isoformat(), "clean": clean}), encoding="utf-8")
    os.replace(tmp, path)


def beat(ctx: AppContext) -> None:
    _write(ctx, clean=False)


def mark_clean_shutdown(ctx: AppContext) -> None:
    _write(ctx, clean=True)


def detect_outage(ctx: AppContext) -> OutageEvent | None:
    """Call once at start: logs the gap since the previous heartbeat, then starts beating."""
    path = _path(ctx)
    event = None
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(data["last"])
        now = ctx.clock.now_utc()
        if now - last > OUTAGE_AFTER:
            event = OutageEvent(started_at_utc=last, ended_at_utc=now, clean_shutdown=bool(data.get("clean")))
            with ctx.uow() as session:
                session.add(event)
    beat(ctx)
    return event
