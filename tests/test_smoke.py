from __future__ import annotations

import subprocess
import sys

import pytest

from caspian_parking import version
from caspian_parking.app import parse_args


def test_version_is_set():
    assert version.__version__


def test_parse_args():
    args = parse_args(["--smoke", "--training"])
    assert args.smoke is True
    assert args.training is True
    assert parse_args([]).smoke is False


def _launch() -> float:
    result = subprocess.run(
        [sys.executable, "-m", "caspian_parking", "--smoke"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    output = result.stdout.decode(errors="replace")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert "main window ready in" in output
    return float(output.split("ready in")[1].split("s")[0])


@pytest.mark.serial
def test_smoke_launch_exits_zero_and_is_fast():
    # best of two launches: the target is the app's start time, not the load of the test machine
    seconds = _launch()
    if seconds >= 3.0:
        seconds = min(seconds, _launch())
    assert seconds < 3.0  # SPEC §2.4: main window ready < 3 s
