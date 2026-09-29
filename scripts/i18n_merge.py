r"""Developer helper: merge new keys into src/caspian_parking/i18n/fa.json.

Usage:  .venv\Scripts\python.exe scripts\i18n_merge.py new_keys.json
Existing keys are overwritten, order of existing keys is kept, new keys are appended.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "src" / "caspian_parking" / "i18n" / "fa.json"


def main(argv: list[str]) -> int:
    new = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    data.update(new)
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(new)} keys merged, catalog has {len(data)} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
