"""Declarative base and the two record families: append-only events and versioned reference data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Boolean, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from caspian_parking.core.ids import uuid7
from caspian_parking.data.types import ID_LENGTH, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        int: Integer(),
        bool: Boolean(),
    }


class EntityMixin:
    """Columns every record carries (SPEC §2.2)."""

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True, default=uuid7)
    origin_node: Mapped[str] = mapped_column(String(ID_LENGTH))
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    created_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)


class EventMixin(EntityMixin):
    """Append-only record: never updated, never deleted (guarded in ORM and by DB triggers)."""

    __append_only__: ClassVar[bool] = True


class ReferenceMixin(EntityMixin):
    """Editable record with optimistic versioning; every change is written to ``audit_log``."""

    __append_only__: ClassVar[bool] = False
    __audited__: ClassVar[bool] = True

    row_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        return {"version_id_col": cls.row_version}  # type: ignore[attr-defined]


def is_append_only(obj_or_cls: object) -> bool:
    cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)
    return bool(getattr(cls, "__append_only__", False))


MoneyColumn = BigInteger
