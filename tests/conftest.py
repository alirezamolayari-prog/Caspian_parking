"""Shared pytest configuration. Qt always runs offscreen in tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
