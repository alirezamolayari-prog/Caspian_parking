from __future__ import annotations

import subprocess
import sys

from caspian_parking import version
from caspian_parking.app import parse_args


def test_version_is_set():
    assert version.__version__


def test_parse_args():
    args = parse_args(["--smoke", "--training"])
    assert args.smoke is True
    assert args.training is True
    assert parse_args([]).smoke is False


def test_smoke_launch_exits_zero_and_is_fast():
    result = subprocess.run(
        [sys.executable, "-m", "caspian_parking", "--smoke"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    output = result.stdout.decode(errors="replace")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert "main window ready in" in output
    seconds = float(output.split("ready in")[1].split("s")[0])
    assert seconds < 3.0  # SPEC §2.4: main window ready < 3 s
