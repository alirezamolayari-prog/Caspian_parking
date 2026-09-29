"""Entry point: ``python -m caspian_parking [--smoke]``."""

from __future__ import annotations

import sys

from caspian_parking.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
