"""Server role without a window (SPEC §2.1, §7): scheduler (daily reports, backups, wallet renewals,
heartbeat), after-hours watch mode for the server's own cameras. Runs as ``python -m caspian_parking
--server`` or as the Windows service in ``services/winservice.py``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from caspian_parking.services.context import AppContext

log = logging.getLogger(__name__)


class ServerHost:
    def __init__(self, ctx: AppContext, source_factory: Callable[..., Any] | None = None) -> None:
        self.ctx = ctx
        self.source_factory = source_factory
        self.scheduler: Any = None
        self.sources: list[Any] = []
        self.passes = 0

    def start(self) -> None:
        from caspian_parking.services.heartbeat import detect_outage
        from caspian_parking.services.scheduler import AppScheduler

        detect_outage(self.ctx)
        self.scheduler = AppScheduler(self.ctx)
        self.scheduler.start()
        self._start_cameras()
        log.info("server host started (%d camera(s))", len(self.sources))

    def _start_cameras(self) -> None:
        from caspian_parking.devices.anpr import AnprError, load_engine
        from caspian_parking.devices.plate_source import create_plate_source

        factory = self.source_factory or create_plate_source
        for config in self.ctx.config.cameras:
            if not config.enabled:
                continue
            engine = None
            if config.kind == "rtsp":
                try:
                    engine = load_engine(config.engine, self.ctx.data_root.sub("anpr"))
                except AnprError as exc:
                    log.warning("ANPR engine for %s: %s", config.lane, exc)
            source = factory(config, self.ctx.clock, engine)
            source.passed.connect(self.on_pass)
            source.start()
            self.sources.append(source)

    def on_pass(self, item: Any) -> None:
        """Watch mode on the server: outside working hours every pass is logged with its photo."""
        from caspian_parking.services.camera import CameraService
        from caspian_parking.services.watch import WatchService

        try:
            after_hours = WatchService(self.ctx).after_hours_now()
            CameraService(self.ctx).record_pass(item, after_hours=after_hours)
            self.passes += 1
        except Exception:  # a storage error must not stop the service
            log.exception("server watch: cannot store camera pass")

    def stop(self) -> None:
        from caspian_parking.services.heartbeat import mark_clean_shutdown

        for source in self.sources:
            source.stop()
        self.sources.clear()
        if self.scheduler is not None:
            self.scheduler.stop()
            self.scheduler = None
        mark_clean_shutdown(self.ctx)
        log.info("server host stopped")


def run_server(data_root: Any = None, stop_after_ms: int | None = None) -> int:
    """Headless loop (Qt core event loop for the camera signals)."""
    from PySide6.QtCore import QCoreApplication, QTimer

    from caspian_parking.services.context import open_context

    app = QCoreApplication.instance() or QCoreApplication([])
    ctx = open_context(data_root)
    host = ServerHost(ctx)
    host.start()
    if stop_after_ms is not None:
        QTimer.singleShot(stop_after_ms, app.quit)
    try:
        return int(app.exec())
    finally:
        host.stop()
        ctx.close()
