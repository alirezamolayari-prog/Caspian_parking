"""Developer helper: (re)generate the bundled Excel templates in src/caspian_parking/resources/templates."""

from __future__ import annotations

import sys
from pathlib import Path

from caspian_parking.services.subscriber_import import write_template

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "caspian_parking" / "resources" / "templates"


def main() -> int:
    path = write_template(TEMPLATES / "subscribers_import.xlsx")
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
