from __future__ import annotations

import subprocess
import sys

from caspian_parking import version
from caspian_parking.app import build_main_window, parse_args


def test_version_is_set():
    assert version.__version__


def test_main_window_builds(qtbot):
    window = build_main_window()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()


def test_parse_smoke_flag():
    assert parse_args(["--smoke"]).smoke is True
    assert parse_args([]).smoke is False


def test_smoke_launch_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "caspian_parking", "--smoke"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
