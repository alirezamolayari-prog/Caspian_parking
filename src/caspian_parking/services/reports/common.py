"""Query helpers shared by the reports."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from caspian_parking.core.plate import plate_from_key
from caspian_parking.data.models import Cancellation, Payment, User, Visit
from caspian_parking.services.exporters import plate_text
from caspian_parking.services.reports.base import ReportParams


def not_cancelled(table: str, column: Any) -> Any:
    return column.not_in(select(Cancellation.target_id).where(Cancellation.target_table == table))


def payments_query(params: ReportParams, *columns: Any) -> Select[Any]:
    stmt = select(*columns).where(
        Payment.created_at_utc >= params.start,
        Payment.created_at_utc < params.end,
        not_cancelled("payments", Payment.id),
    )
    if params.gate_code is not None:
        stmt = stmt.where(Payment.gate_code == params.gate_code)
    if params.operator_id:
        stmt = stmt.where(Payment.created_by == params.operator_id)
    return stmt


def visits_query(params: ReportParams, by_exit: bool = False) -> Select[Any]:
    column = Visit.exit_at_utc if by_exit else Visit.entry_at_utc
    stmt = select(Visit).where(column >= params.start, column < params.end)
    if params.gate_code is not None:
        stmt = stmt.where(or_(Visit.gate_in == params.gate_code, Visit.gate_out == params.gate_code))
    if params.operator_id:
        stmt = stmt.where(Visit.created_by == params.operator_id)
    if params.category:
        stmt = stmt.where(Visit.category == params.category)
    return stmt


def user_names(session: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in session.scalars(select(User))}


def plate_cell(key: str | None) -> str:
    if not key:
        return "—"
    try:
        return plate_text(plate_from_key(key))
    except ValueError:
        return key
