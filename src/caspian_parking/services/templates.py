"""Custom receipt templates (SPEC §4.11): PNG/JPG header images in ``<data root>\\templates``.

Sub-folders are categories and the display name is the file name (without extension). The UI watches the
folder (QFileSystemWatcher) and calls ``list_templates`` again on every change. The selected template is
stored as a path relative to the folder; when that file disappears the mother receipt is used and the
caller shows a warning.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting, set_setting

EXTENSIONS = (".png", ".jpg", ".jpeg")
SETTING = "receipt.template"


@dataclass(frozen=True)
class Template:
    name: str  # file name without extension
    category: str  # sub-folder ("" = top level)
    relative: str  # path relative to the templates folder, with "/" separators
    path: Path


def list_templates(folder: Path) -> list[Template]:
    if not folder.is_dir():
        return []
    result = []
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            relative = path.relative_to(folder)
            category = relative.parent.as_posix()
            result.append(Template(path.stem, "" if category == "." else category, relative.as_posix(), path))
    result.sort(key=lambda t: (t.category, t.name))
    return result


def search(templates: list[Template], text: str) -> list[Template]:
    needle = text.strip().casefold()
    if not needle:
        return templates
    return [t for t in templates if needle in t.name.casefold() or needle in t.category.casefold()]


def categories(templates: list[Template]) -> list[str]:
    return sorted({t.category for t in templates})


@dataclass(frozen=True)
class Selection:
    relative: str  # "" = mother receipt
    path: Path | None  # None when the mother receipt is used
    missing: bool  # a template was selected but its file is gone (fallback + warning)


def selected_template(ctx: AppContext) -> Selection:
    with ctx.read() as session:
        relative = str(get_setting(session, SETTING) or "")
    if not relative:
        return Selection("", None, False)
    path = ctx.data_root.templates / relative
    if not path.is_file():
        return Selection(relative, None, True)
    return Selection(relative, path, False)


def select_template(ctx: AppContext, relative: str | None) -> None:
    """Choose a template (``None`` / "" = back to the mother receipt)."""
    with ctx.uow() as session:
        set_setting(session, SETTING, relative or "")


def open_in_paint(path: Path) -> subprocess.Popen[bytes] | None:  # pragma: no cover - opens a Windows program
    if os.name != "nt":
        return None
    return subprocess.Popen(["mspaint.exe", str(path)])
