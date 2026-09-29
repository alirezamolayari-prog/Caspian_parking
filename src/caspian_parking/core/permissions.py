"""Granular permissions (SPEC §4.1) and the built-in role presets."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    OPERATE_GATE = "gate.operate"
    CHANGE_TARIFFS = "tariffs.change"
    ADJUST_NIGHT_FINES = "fines.adjust"
    ADJUST_AMOUNTS = "amounts.adjust"
    CANCEL_TRANSACTIONS = "transactions.cancel"
    GRANT_GUEST = "guest.grant"
    ALLOW_NEGATIVE_SUBSCRIPTION = "subscription.negative"
    MANAGE_BLOCKLIST = "blocklist.manage"
    VIEW_BLOCK_DETAILS = "blocklist.details"
    MANAGE_SUBSCRIBERS = "subscribers.manage"
    MANAGE_SHOPS = "shops.manage"
    MANAGE_ADS = "ads.manage"
    VIEW_REPORTS = "reports.view"
    VIEW_FINANCIAL_REPORTS = "reports.financial"
    EXPORT_REPORTS = "reports.export"
    MANAGE_USERS = "users.manage"
    VIEW_AUDIT = "audit.view"
    BACKUP_RESTORE = "backup.manage"
    CLOSE_FISCAL_YEAR = "fiscal.close"
    CHANGE_SETTINGS = "settings.change"
    HARDWARE_SETTINGS = "hardware.settings"
    RESOLVE_REVIEW = "review.resolve"


ALL_PERMISSIONS: frozenset[str] = frozenset(p.value for p in Permission)

OPERATOR_PERMISSIONS: frozenset[str] = frozenset({Permission.OPERATE_GATE, Permission.VIEW_REPORTS})

SUPERVISOR_PERMISSIONS: frozenset[str] = OPERATOR_PERMISSIONS | frozenset(
    {
        Permission.ADJUST_NIGHT_FINES,
        Permission.ADJUST_AMOUNTS,
        Permission.CANCEL_TRANSACTIONS,
        Permission.GRANT_GUEST,
        Permission.ALLOW_NEGATIVE_SUBSCRIPTION,
        Permission.MANAGE_BLOCKLIST,
        Permission.VIEW_BLOCK_DETAILS,
        Permission.MANAGE_SUBSCRIBERS,
        Permission.MANAGE_SHOPS,
        Permission.VIEW_FINANCIAL_REPORTS,
        Permission.EXPORT_REPORTS,
        Permission.RESOLVE_REVIEW,
        Permission.VIEW_AUDIT,
    }
)

BUILTIN_PRESETS: dict[str, frozenset[str]] = {
    "operator": OPERATOR_PERMISSIONS,
    "supervisor": SUPERVISOR_PERMISSIONS,
    "admin": ALL_PERMISSIONS,
}


def normalize_permissions(values: object) -> list[str]:
    """Known permission codes only, sorted, without duplicates."""
    if not isinstance(values, list | tuple | set | frozenset):
        return []
    return sorted({str(v) for v in values} & ALL_PERMISSIONS)
