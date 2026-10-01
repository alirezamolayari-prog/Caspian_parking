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
    "receipt.template": "",
    "gate.preview_before_print": False,
    "gate.print_for_covered": False,  # print a paper ticket for subscribers / free access too
    "subscription.amber_days": 5,
    "subscription.red_hours": 48,
    "subscription.negative_max_days": 10,
    "wallet.auto_renew": True,
    "reports.daily_enabled": True,
    "reports.daily_time": "21:00",
    "reports.daily_folder": "",
    "reports.daily_formats": ["excel", "word"],
    "reports.daily_last": "",
    "backup.enabled": True,
    "backup.time": "22:00",
    "backup.keep": 30,
    "backup.on_close": True,
    "photos.retention_days": 90,
    "maintenance.last_run": "",
    "disk.min_free_percent": 5,
    "ads.packages": {},
    "ads.slots_per_day": 3,
    "ads.advertiser_discount_percent": 10,
    "ads.occasions": [],
    "coupons.unit_price": 0,
    "coupons.expiry_days": 30,
    "adscreen.seconds": 8,
    "update.share": "",
}


def get_setting(session: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key}")
    return SettingsRepository(session).get(key, DEFAULTS[key])


def set_setting(session: Session, key: str, value: Any, reason: str | None = None) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key}")
    SettingsRepository(session).set(key, value, reason=reason)
