"""Developer helper: autogenerate an Alembic revision from the current models.

Usage:  .venv\\Scripts\\python.exe scripts\\new_migration.py "short message" [revision_id]

It upgrades a temporary SQLite database to head, compares it with the models and writes a
new file to ``src/caspian_parking/data/migrations/versions``. Review the file before committing
(add append-only triggers for new event tables, check SQL Server compatibility).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from alembic import command

from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.data.migrate import alembic_config, upgrade


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    message = argv[0]
    rev_id = argv[1] if len(argv) > 1 else None
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_sqlite_engine(Path(tmp) / "autogen.db")
        upgrade(engine)
        with engine.begin() as connection:
            command.revision(alembic_config(connection), message=message, autogenerate=True, rev_id=rev_id)
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
