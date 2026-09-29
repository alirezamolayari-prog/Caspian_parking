"""Parking coupons (SPEC §4.11): shops buy N single-use codes, used at exit to zero the parking fee.

Paid directly (a ``Payment`` with purpose ``coupon``) or from the shop wallet (a ``WalletTransaction``).
Using a coupon writes a ``CouponRedemption`` event (unique per coupon), so a code can never be used twice.
Night fines still apply (the tariff engine's coupon flag only zeroes the parking fee).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.coupons import is_coupon_code, new_coupon_code, normalize_coupon_code
from caspian_parking.core.jalali import local_date
from caspian_parking.core.money import require_int
from caspian_parking.core.permissions import Permission
from caspian_parking.data.models import Coupon, CouponBatch, CouponRedemption, Payment, Shop, WalletTransaction
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting

MAX_BATCH = 1000
METHODS = ("cash", "card", "mall_card", "wallet")


class CouponError(RuntimeError):
    pass


class CouponStatus(StrEnum):
    VALID = "valid"
    USED = "used"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CouponBatchRepository(ReferenceRepository[CouponBatch]):
    model = CouponBatch


@dataclass(frozen=True)
class ShopCouponStats:
    shop_id: str
    shop_name: str
    bought: int
    used: int
    expired: int
    revenue: int

    @property
    def open(self) -> int:
        return self.bought - self.used - self.expired


def _wallet_balance(session: Session, shop_id: str) -> int:
    total = session.scalar(
        select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(WalletTransaction.shop_id == shop_id)
    )
    return int(total or 0)


def _unique_codes(session: Session, quantity: int) -> list[str]:
    codes: set[str] = set()
    while len(codes) < quantity:
        fresh = {new_coupon_code() for _ in range(quantity - len(codes))} - codes
        taken = set(session.scalars(select(Coupon.code).where(Coupon.code.in_(fresh)))) if fresh else set()
        codes |= fresh - taken
    return sorted(codes)


def coupon_status(session: Session, coupon: Coupon, today: date) -> CouponStatus:
    if session.scalar(
        select(func.count()).select_from(CouponRedemption).where(CouponRedemption.coupon_id == coupon.id)
    ):
        return CouponStatus.USED
    if not coupon.is_active:
        return CouponStatus.CANCELLED
    if coupon.expires_on < today:
        return CouponStatus.EXPIRED
    return CouponStatus.VALID


def check_coupon(session: Session, text: str, today: date) -> Coupon:
    """The coupon for a scanned/typed code, or ``CouponError`` (invalid / used / expired / cancelled)."""
    code = normalize_coupon_code(text)
    if not is_coupon_code(code):
        raise CouponError("coupons.invalid")
    coupon = session.scalar(select(Coupon).where(Coupon.code == code))
    if coupon is None:
        raise CouponError("coupons.invalid")
    status = coupon_status(session, coupon, today)
    if status is not CouponStatus.VALID:
        raise CouponError(f"coupons.{status.value}")
    return coupon


class CouponService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def _today(self) -> date:
        return local_date(self.ctx.clock.now_utc())

    def sell_batch(
        self, shop_id: str, quantity: int, method: str, template_file: str | None = None, reference: str | None = None
    ) -> tuple[CouponBatch, list[Coupon]]:
        if not self.ctx.can(Permission.MANAGE_ADS):
            raise CouponError("gate.permission_denied")
        require_int(quantity)
        if not 1 <= quantity <= MAX_BATCH:
            raise CouponError("coupons.bad_quantity")
        if method not in METHODS:
            raise CouponError("gate.method_required")
        with self.ctx.uow() as session:
            if session.get(Shop, shop_id) is None:
                raise CouponError("people.not_found")
            unit_price = int(get_setting(session, "coupons.unit_price"))
            expiry_days = int(get_setting(session, "coupons.expiry_days"))
            total = unit_price * quantity
            if method == "wallet" and _wallet_balance(session, shop_id) < total:
                raise CouponError("coupons.low_balance")
            expires_on = self._today() + timedelta(days=expiry_days)
            batch = CouponBatchRepository(session).add(
                CouponBatch(
                    shop_id=shop_id,
                    quantity=quantity,
                    unit_price=unit_price,
                    total=total,
                    method=method,
                    expires_on=expires_on,
                    template_file=template_file,
                )
            )
            coupons = [
                Coupon(batch_id=batch.id, shop_id=shop_id, code=code, expires_on=expires_on)
                for code in _unique_codes(session, quantity)
            ]
            session.add_all(coupons)
            if total > 0:
                if method == "wallet":
                    session.add(WalletTransaction(shop_id=shop_id, kind="coupon", amount=-total, reference_id=batch.id))
                else:
                    session.add(
                        Payment(
                            purpose="coupon",
                            method=method,
                            amount=total,
                            gate_code=self.ctx.config.gate_code,
                            reference=reference or batch.id,
                        )
                    )
            session.flush()
            return batch, coupons

    def batches(self, shop_id: str | None = None) -> list[CouponBatch]:
        with self.ctx.read() as session:
            stmt = select(CouponBatch).order_by(CouponBatch.created_at_utc.desc())
            if shop_id:
                stmt = stmt.where(CouponBatch.shop_id == shop_id)
            return list(session.scalars(stmt))

    def coupons_of(self, batch_id: str) -> list[tuple[Coupon, CouponStatus]]:
        today = self._today()
        with self.ctx.read() as session:
            rows = session.scalars(select(Coupon).where(Coupon.batch_id == batch_id).order_by(Coupon.code)).all()
            return [(c, coupon_status(session, c, today)) for c in rows]

    def check(self, text: str) -> Coupon:
        with self.ctx.read() as session:
            return check_coupon(session, text, self._today())

    def stats(self) -> list[ShopCouponStats]:
        with self.ctx.read() as session:
            return shop_coupon_stats(session, self._today())


def shop_coupon_stats(session: Session, today: date, shop_id: str | None = None) -> list[ShopCouponStats]:
    """Per shop: coupons bought / used / expired unused, and revenue (SPEC §4.11 coupon report)."""
    names = {s.id: s.name for s in session.scalars(select(Shop))}
    bought: dict[str, int] = {}
    revenue: dict[str, int] = {}
    stmt = select(CouponBatch.shop_id, func.sum(CouponBatch.quantity), func.sum(CouponBatch.total)).group_by(
        CouponBatch.shop_id
    )
    if shop_id:
        stmt = stmt.where(CouponBatch.shop_id == shop_id)
    for shop, quantity, total in session.execute(stmt):
        bought[shop] = int(quantity or 0)
        revenue[shop] = int(total or 0)
    used = dict(
        session.execute(
            select(Coupon.shop_id, func.count())
            .join(CouponRedemption, CouponRedemption.coupon_id == Coupon.id)
            .group_by(Coupon.shop_id)
        ).all()
    )
    redeemed = select(CouponRedemption.coupon_id)
    expired = dict(
        session.execute(
            select(Coupon.shop_id, func.count())
            .where(Coupon.expires_on < today, Coupon.id.not_in(redeemed))
            .group_by(Coupon.shop_id)
        ).all()
    )
    return [
        ShopCouponStats(
            shop, names.get(shop, "—"), bought[shop], int(used.get(shop, 0)), int(expired.get(shop, 0)), revenue[shop]
        )
        for shop in sorted(bought, key=lambda s: names.get(s, ""))
    ]
