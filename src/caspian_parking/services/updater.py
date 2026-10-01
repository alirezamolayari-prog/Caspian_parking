"""Auto-update (SPEC §7): gates look in a shared folder on the server at start-up and install a newer
version silently. The folder holds ``version.json``::

    {"version": "1.2.0", "installer": "Parking-Setup-1.2.0.exe", "notes": "…"}

The update is applied at start-up only, before anyone logs in, so no transaction can be open.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from caspian_parking.version import __version__

log = logging.getLogger(__name__)

MANIFEST = "version.json"
SILENT_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    installer: Path
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(share: Path | str, current: str = __version__) -> UpdateInfo | None:
    """A newer version in the share, or None (missing share / manifest / installer are not errors)."""
    folder = Path(share)
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        version = str(data["version"])
        installer = folder / str(data["installer"])
    except (OSError, ValueError, KeyError) as exc:
        log.info("no update information in %s: %s", folder, exc)
        return None
    if parse_version(version) <= parse_version(current) or not installer.is_file():
        return None
    return UpdateInfo(version, installer, str(data.get("notes", "")))


def install_command(info: UpdateInfo) -> list[str]:
    return [str(info.installer), *SILENT_ARGS]


def apply_update(info: UpdateInfo, launcher: Callable[[list[str]], Any] = subprocess.Popen) -> None:
    """Start the installer and let the app exit; the installer restarts the app when it is done."""
    log.info("installing update %s from %s", info.version, info.installer)
    launcher(install_command(info))
