"""Runs gate ↔ server sync every few seconds on a worker thread and shows the result:
link indicator in the top bar, alert bar entries for link down and clock drift (SPEC §2.2, §4.15)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, fa_time
from caspian_parking.services.context import AppContext
from caspian_parking.services.sync import SyncEngine, SyncStatus
from caspian_parking.ui.widgets.feedback import Alert
from caspian_parking.ui.workers import run_in_background

log = logging.getLogger(__name__)

SYNC_INTERVAL_MS = 15_000


class SyncController(QObject):
    status_changed = Signal(object)  # SyncStatus

    def __init__(
        self,
        ctx: AppContext,
        window: object,
        engine: SyncEngine | None = None,
        interval_ms: int = SYNC_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.window = window
        if engine is None:
            assert ctx.server_engine is not None
            engine = SyncEngine(ctx, ctx.server_engine)
        self.engine = engine
        self.busy = False
        self.last: SyncStatus | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.sync_now)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(0, self.sync_now)

    def stop(self) -> None:
        self.timer.stop()

    def sync_now(self) -> None:
        if self.busy:
            return
        self.busy = True
        run_in_background(self.engine.sync_once, self.apply_status, self._failed)

    def sync_blocking(self) -> SyncStatus:
        """Same as one timer tick, on the calling thread (tests, 'sync now' before closing)."""
        status = self.engine.sync_once()
        self.apply_status(status)
        return status

    def _failed(self, error: Exception) -> None:
        log.exception("sync worker crashed", exc_info=error)
        self.apply_status(SyncStatus(False, self.last.last_sync if self.last else None, error=str(error)))

    def apply_status(self, status: SyncStatus) -> None:
        self.busy = False
        self.last = status
        self.ctx.link_online = status.online
        topbar = getattr(self.window, "topbar", None)
        if topbar is not None:
            topbar.set_link_status(status.online, fa_time(status.last_sync) if status.last_sync else None)
        raise_alert: Callable[[Alert], None] | None = getattr(getattr(self.window, "alerts", None), "raise_alert", None)
        clear_alert: Callable[[str], None] | None = getattr(getattr(self.window, "alerts", None), "clear_alert", None)
        if raise_alert is not None and clear_alert is not None:
            if status.online:
                clear_alert("link")
            else:
                raise_alert(Alert("link", tr("link.down_alert", n=fa_digits(status.pending)), "warning"))
            if status.drift_warning:
                drift = round(abs(status.drift_seconds or 0))
                raise_alert(Alert("clock", tr("link.clock_drift", n=fa_digits(drift)), "warning"))
            elif status.online:
                clear_alert("clock")
        if status.pulled:
            page = getattr(self.window, "page", None)
            gate = page("gate") if callable(page) else None
            if gate is not None and hasattr(gate, "refresh_lists"):
                gate.refresh_lists()
        self.status_changed.emit(status)
