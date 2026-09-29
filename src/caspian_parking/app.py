"""Application bootstrap."""

from __future__ import annotations

import argparse

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

SMOKE_EXIT_MS = 300


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="caspian_parking")
    parser.add_argument("--smoke", action="store_true", help="open the main window, then exit with code 0")
    return parser.parse_args(argv)


def build_main_window() -> QMainWindow:
    window = QMainWindow()
    window.setCentralWidget(QLabel())
    window.resize(1280, 800)
    return window


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or [])
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    window = build_main_window()
    window.show()
    if args.smoke:
        QTimer.singleShot(SMOKE_EXIT_MS, app.quit)
    return app.exec()
