"""Screen registry: what appears in the sidebar and the command palette, and who may see it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from caspian_parking.services.context import AppContext


@dataclass(frozen=True)
class ScreenSpec:
    key: str
    title_key: str
    icon: str
    factory: Callable[[AppContext], QWidget]
    permission: str | None = None
    shortcut: str | None = None
    keywords: tuple[str, ...] = field(default_factory=tuple)
    section: str = "main"


_REGISTRY: list[ScreenSpec] = []


def register(spec: ScreenSpec) -> ScreenSpec:
    for index, existing in enumerate(_REGISTRY):
        if existing.key == spec.key:
            _REGISTRY[index] = spec
            return spec
    _REGISTRY.append(spec)
    return spec


def all_screens() -> list[ScreenSpec]:
    from caspian_parking.ui.screens import register_builtin_screens

    register_builtin_screens()
    return list(_REGISTRY)


def visible_screens(can: Callable[[str], bool]) -> list[ScreenSpec]:
    return [spec for spec in all_screens() if spec.permission is None or can(spec.permission)]
