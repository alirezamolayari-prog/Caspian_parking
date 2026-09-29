"""Hardware / system alerts (SPEC §4.15), shown as a non-blocking bar and logged.

Phase 6 checks: printer, backup overdue, disk space. Later phases add camera, server link,
clock drift and missing template checks through ``register_check``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from caspian_parking.services.context import AppContext

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemAlert:
    key: str
    text_key: str
    level: str = "warning"  # info | warning | danger
    params: dict[str, str] = field(default_factory=dict)


Check = Callable[[AppContext], list[SystemAlert]]
_CHECKS: list[Check] = []


def register_check(check: Check) -> Check:
    if check not in _CHECKS:
        _CHECKS.append(check)
    return check


@register_check
def backup_check(ctx: AppContext) -> list[SystemAlert]:
    from caspian_parking.services.backup import overdue

    return [SystemAlert("backup", "alert.backup_overdue")] if overdue(ctx) else []


@register_check
def disk_check(ctx: AppContext) -> list[SystemAlert]:
    from caspian_parking.services.photos import disk_low

    return [SystemAlert("disk", "alert.disk_low", "danger")] if disk_low(ctx) else []


def printer_alerts(status: object) -> list[SystemAlert]:
    online = getattr(status, "online", True)
    paper_ok = getattr(status, "paper_ok", True)
    if not online:
        return [SystemAlert("printer", "printer.offline", "danger")]
    if not paper_ok:
        return [SystemAlert("printer", "printer.paper_out", "danger")]
    return []


class AlertMonitor:
    """Runs all checks; remembers what is active so each alert is logged once when it appears."""

    def __init__(self, ctx: AppContext, extra: list[Callable[[], list[SystemAlert]]] | None = None) -> None:
        self.ctx = ctx
        self.extra = extra or []
        self.active: dict[str, SystemAlert] = {}

    def evaluate(self) -> tuple[list[SystemAlert], list[str]]:
        """Returns (current alerts, keys that cleared)."""
        current: dict[str, SystemAlert] = {}
        for check in _CHECKS:
            try:
                for alert in check(self.ctx):
                    current[alert.key] = alert
            except Exception:  # a failing check must never break the UI
                log.exception("alert check %s failed", getattr(check, "__name__", check))
        for extra in self.extra:
            for alert in extra():
                current[alert.key] = alert
        for key, alert in current.items():
            if key not in self.active:
                log.warning("alert raised: %s (%s)", key, alert.text_key)
        cleared = [key for key in self.active if key not in current]
        for key in cleared:
            log.info("alert cleared: %s", key)
        self.active = current
        return list(current.values()), cleared
