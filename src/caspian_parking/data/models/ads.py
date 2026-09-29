"""Advertising (SPEC §4.11): ad contracts, print log, coupons, monthly raffle."""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from sqlalchemy import BigInteger, Boolean, Date, Index, Integer, String, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime


class Ad(ReferenceMixin, Base):
    """An ad contract: what is printed on receipts, when, and what the shop paid."""

    __tablename__ = "ads"
    __table_args__ = (Index("ix_ads_dates", "start_date", "end_date"),)

    shop_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    package: Mapped[str] = mapped_column(String(20))  # bronze | silver | gold
    text: Mapped[str] = mapped_column(UnicodeText, default="")
    offer: Mapped[str] = mapped_column(Unicode(200), default="")
    location: Mapped[str] = mapped_column(Unicode(120), default="")
    large: Mapped[bool] = mapped_column(Boolean, default=False)
    logo_file: Mapped[str | None] = mapped_column(Unicode(260), default=None)  # inside <data root>/ads
    on_entry: Mapped[bool] = mapped_column(Boolean, default=True)
    on_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    weekdays: Mapped[list[int]] = mapped_column(JSONText(), default=list)  # empty = every day
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # inclusive
    price: Mapped[int] = mapped_column(BigInteger, default=0)


class AdPrint(EventMixin, Base):
    """One printed ad (proof of delivery for the shop)."""

    __tablename__ = "ad_prints"
    __table_args__ = (Index("ix_ad_prints_ad", "ad_id", "created_at_utc"),)

    ad_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    kind: Mapped[str] = mapped_column(String(10))  # entry | exit | coupon
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)


class CouponBatch(ReferenceMixin, Base):
    """Coupons bought by a shop (paid directly or from its wallet)."""

    __tablename__ = "coupon_batches"
    __table_args__ = (Index("ix_coupon_batches_shop", "shop_id"),)

    shop_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(20))  # cash | card | mall_card | wallet
    expires_on: Mapped[date] = mapped_column(Date)
    template_file: Mapped[str | None] = mapped_column(Unicode(260), default=None)


class Coupon(ReferenceMixin, Base):
    """A single-use coupon. Never updated: using it writes a ``CouponRedemption`` event."""

    __tablename__ = "coupons"
    __audited__: ClassVar[bool] = False  # created in batches; the batch is audited
    __table_args__ = (Index("ix_coupons_batch", "batch_id"),)

    batch_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    shop_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    expires_on: Mapped[date] = mapped_column(Date)


class CouponRedemption(EventMixin, Base):
    __tablename__ = "coupon_redemptions"
    __table_args__ = (Index("ix_coupon_redemptions_coupon", "coupon_id"),)

    coupon_id: Mapped[str] = mapped_column(String(ID_LENGTH), unique=True)
    session_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    discount: Mapped[int] = mapped_column(BigInteger)


class RaffleDraw(EventMixin, Base):
    """Monthly raffle among transient receipt numbers (cryptographically random, logged)."""

    __tablename__ = "raffle_draws"

    month: Mapped[str] = mapped_column(String(7))  # Jalali yyyy-mm
    sponsor_shop_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    candidates: Mapped[int] = mapped_column(Integer)
    candidates_digest: Mapped[str] = mapped_column(String(64))
    winner_ticket: Mapped[str] = mapped_column(String(24))
    winner_entry_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
