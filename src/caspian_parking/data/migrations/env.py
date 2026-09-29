"""Alembic environment. Always run programmatically via ``caspian_parking.data.migrate``."""

from __future__ import annotations

from alembic import context

import caspian_parking.data.models  # noqa: F401 - registers all tables
from caspian_parking.data.base import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("migrations must be run through caspian_parking.data.migrate")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
