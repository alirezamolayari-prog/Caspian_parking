"""Windows service wrapper for the server role (pywin32). Installed by the setup program:

    parking.exe --service install      (needs an administrator; the installer runs it)
    parking.exe --service start | stop | remove

The service starts automatically with Windows and restarts the host after a crash (recovery options are
set by the installer). Names come from ``app_defaults.json`` (no brand strings in code).
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

log = logging.getLogger(__name__)

SERVICE_SUFFIX = "Server"


def service_names() -> tuple[str, str]:
    from caspian_parking.config.defaults import app_defaults, product_name

    folder = str(app_defaults()["product_folder"])
    return f"{folder}{SERVICE_SUFFIX}", f"{product_name()} — {SERVICE_SUFFIX}"


def handle_command_line(argv: list[str]) -> int:  # pragma: no cover - needs Windows service control
    import win32serviceutil

    service_class = _service_class()
    win32serviceutil.HandleCommandLine(service_class, argv=[sys.argv[0], *argv])
    return 0


def _service_class() -> Any:  # pragma: no cover - runs inside the Windows service manager
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    name, display = service_names()

    class ParkingService(win32serviceutil.ServiceFramework):
        _svc_name_ = name
        _svc_display_name_ = display
        _svc_description_ = display

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.stopping = threading.Event()

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stopping.set()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self) -> None:
            from PySide6.QtCore import QCoreApplication, QTimer

            from caspian_parking.services.context import open_context
            from caspian_parking.services.server_host import ServerHost

            servicemanager.LogInfoMsg(f"{display} starting")
            app = QCoreApplication.instance() or QCoreApplication([])
            ctx = open_context()
            host = ServerHost(ctx)
            host.start()
            timer = QTimer()
            timer.timeout.connect(lambda: app.quit() if self.stopping.is_set() else None)
            timer.start(1000)
            try:
                app.exec()
            finally:
                host.stop()
                ctx.close()
                servicemanager.LogInfoMsg(f"{display} stopped")

    return ParkingService
