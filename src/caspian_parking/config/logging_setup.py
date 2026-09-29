"""Rotating, size-capped log files in ``<data root>\\logs``."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_FILE = "app.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 10
_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(logs_dir: Path, level: int = logging.INFO, console: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_parking_handler", False):
            root.removeHandler(handler)
            handler.close()
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._parking_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(_FORMAT))
        stream._parking_handler = True  # type: ignore[attr-defined]
        root.addHandler(stream)
    root.setLevel(level)
