"""Typed access to installation settings with defaults (values live in the ``settings`` table)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from caspian_parking.data.repositories.system import SettingsRepository

DEFAULTS: dict[str, Any] = {
    "site.mall_name": "",
    "site.mall_name_en": "",
    "security.password_min_length": 6,
    "gate.debounce_seconds": 3,
    "ui.follow_windows_theme": False,
    "receipt.show_logo": True,
    "receipt.show_ad": True,
    "receipt.show_barcode_art": True,
    "receipt.barcode_art": "tree.png",
    "receipt.divider_image": False,
    "receipt.labels": {},
    "receipt.print_exit_receipt": False,
    "gate.preview_before_print": False,
    "gate.print_for_covered": False,  # print a paper ticket for subscribers / free access too
    "subscription.amber_days": 5,
    "subscription.red_hours": 48,
    "subscription.negative_max_days": 10,
    "wallet.auto_renew": True,
}


def get_setting(session: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key}")
    return SettingsRepository(session).get(key, DEFAULTS[key])


def set_setting(session: Session, key: str, value: Any, reason: str | None = None) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key}")
    SettingsRepository(session).set(key, value, reason=reason)
