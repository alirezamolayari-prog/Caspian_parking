"""'Needs review' queue (SPEC §2.2): possible duplicates entered offline and sync conflicts.

The supervisor decides; the app never cancels or deletes anything on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import AuditLog, Cancellation, Node, ReviewItem, SubscriptionPayment, WalletTransaction
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.services.context import AppContext


class ReviewError(RuntimeError):
    pass


class ReviewRepository(ReferenceRepository[ReviewItem]):
    model = ReviewItem


@dataclass(frozen=True)
class ReviewRow:
    """One record behind a review item (a payment, a deposit, or the lost version of a record)."""

    id: str
    node: str
    when: Any
    amount: int | None
    cancelled: bool
    details: dict[str, Any]


class ReviewService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def _require(self) -> None:
        if not self.ctx.can(Permission.CANCEL_TRANSACTIONS):
            raise ReviewError("gate.permission_denied")

    def items(self, status: str = "open") -> list[ReviewItem]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(ReviewItem).where(ReviewItem.status == status).order_by(ReviewItem.created_at_utc.desc())
                )
            )

    def open_count(self) -> int:
        with self.ctx.read() as session:
            return int(
                session.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "open")) or 0
            )

    def rows(self, item: ReviewItem) -> list[ReviewRow]:
        with self.ctx.read() as session:
            nodes = {n.id: n.name for n in session.scalars(select(Node))}
            if item.kind in ("duplicate_payment", "duplicate_deposit"):
                table = item.refs.get("table")
                model: Any = SubscriptionPayment if table == "subscription_payments" else WalletTransaction
                cancelled = set(
                    session.scalars(select(Cancellation.target_id).where(Cancellation.target_table == table))
                )
                records: list[Any] = list(session.scalars(select(model).where(model.id.in_(item.refs.get("ids", [])))))
                return [
                    ReviewRow(
                        r.id,
                        nodes.get(r.origin_node, r.origin_node[:8]),
                        r.created_at_utc,
                        int(r.amount),
                        r.id in cancelled,
                        {"method": getattr(r, "method", None)},
                    )
                    for r in sorted(records, key=lambda r: r.created_at_utc)
                ]
            lost = session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "sync_conflict",
                    AuditLog.entity == item.refs.get("table"),
                    AuditLog.entity_id == item.refs.get("row_id"),
                )
                .order_by(AuditLog.created_at_utc)
            ).all()
            return [
                ReviewRow(a.id, nodes.get(a.origin_node, a.origin_node[:8]), a.created_at_utc, None, False, a.changes)
                for a in lost
            ]

    def resolve(self, item_id: str, resolution: str) -> ReviewItem:
        self._require()
        if not resolution.strip():
            raise ReviewError("gate.reason_required")
        with self.ctx.uow(reason=resolution) as session:
            item = session.get(ReviewItem, item_id)
            if item is None:
                raise ReviewError("people.not_found")
            return ReviewRepository(session).update(item, status="resolved", resolution=resolution.strip())

    def cancel_payment(self, item: ReviewItem, payment_id: str, reason: str) -> None:
        """Cancel one of the duplicate subscription payments (the item stays open until resolved)."""
        from caspian_parking.services.people import PeopleError, PeopleService

        self._require()
        if item.kind != "duplicate_payment" or payment_id not in item.refs.get("ids", []):
            raise ReviewError("review.not_in_item")
        try:
            PeopleService(self.ctx).cancel_subscription_payment(payment_id, reason)
        except PeopleError as exc:
            raise ReviewError(str(exc)) from exc
