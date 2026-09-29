"""Subscribers, free access, shops & wallets, blocklist (SPEC §4.6–4.8)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from caspian_parking.data.base import Base, EventMixin, ReferenceMixin
from caspian_parking.data.models.gate import PLATE_KEY_LENGTH
from caspian_parking.data.types import ID_LENGTH, JSONText, UTCDateTime


class Shop(ReferenceMixin, Base):
    """Shop account (حساب مغازه): groups the people of one brand and owns a wallet."""

    __tablename__ = "shops"

    name: Mapped[str] = mapped_column(Unicode(120))
    location_type: Mapped[str] = mapped_column(String(10), default="inside")  # inside | outside (the mall)
    unit: Mapped[str | None] = mapped_column(Unicode(120), default=None)
    phone: Mapped[str | None] = mapped_column(Unicode(30), default=None)
    contact_name: Mapped[str | None] = mapped_column(Unicode(120), default=None)
    notes: Mapped[str | None] = mapped_column(Unicode(500), default=None)
    low_balance_threshold: Mapped[int] = mapped_column(BigInteger, default=0)


class WalletTransaction(EventMixin, Base):
    """Shop wallet movement. ``amount`` is signed: deposits > 0, debits < 0."""

    __tablename__ = "wallet_transactions"
    __table_args__ = (Index("ix_wallet_transactions_shop", "shop_id", "created_at_utc"),)

    shop_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    kind: Mapped[str] = mapped_column(String(20))  # deposit | subscription | coupon | adjustment
    amount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str | None] = mapped_column(String(20), default=None)
    person_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    reference_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    note: Mapped[str | None] = mapped_column(Unicode(300), default=None)


class Person(ReferenceMixin, Base):
    """A subscriber or a free-access person (staff, mall owner, guest)."""

    __tablename__ = "people"
    __table_args__ = (Index("ix_people_kind", "kind"), Index("ix_people_shop", "shop_id"))

    kind: Mapped[str] = mapped_column(String(20))  # subscriber | free
    free_category: Mapped[str | None] = mapped_column(String(20), default=None)  # staff | owner | guest
    first_name: Mapped[str] = mapped_column(Unicode(80))
    last_name: Mapped[str] = mapped_column(Unicode(80), default="")
    mobile: Mapped[str | None] = mapped_column(String(20), default=None)
    shop_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    brand: Mapped[str | None] = mapped_column(Unicode(120), default=None)
    location_type: Mapped[str] = mapped_column(String(10), default="inside")
    vehicle_model: Mapped[str | None] = mapped_column(Unicode(80), default=None)
    notes: Mapped[str | None] = mapped_column(Unicode(500), default=None)
    payer: Mapped[str] = mapped_column(String(10), default="self")  # self | shop
    price_override: Mapped[int | None] = mapped_column(BigInteger, default=None)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1)
    subscription_end_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    negative_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    negative_max_days: Mapped[int] = mapped_column(Integer, default=10)
    negative_approved_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    night_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    night_exempt_approved_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    approved_by: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)  # free access approver

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class PersonPlate(ReferenceMixin, Base):
    __tablename__ = "person_plates"
    __table_args__ = (Index("ix_person_plates_plate", "plate_key"), Index("ix_person_plates_person", "person_id"))

    person_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    plate_key: Mapped[str] = mapped_column(Unicode(PLATE_KEY_LENGTH))
    description: Mapped[str | None] = mapped_column(Unicode(120), default=None)


class SubscriptionPayment(EventMixin, Base):
    __tablename__ = "subscription_payments"
    __table_args__ = (Index("ix_subscription_payments_person", "person_id", "paid_at_utc"),)

    person_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    amount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(20))  # cash | card | mall_card | wallet
    shop_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    paid_at_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    period_days: Mapped[int] = mapped_column(Integer)
    previous_end_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    new_end_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    used_days: Mapped[list[str]] = mapped_column(JSONText(), default=list)
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)


class GuestPermit(ReferenceMixin, Base):
    """Guest free access for a date range (one-time or multi-day), auto-expiring."""

    __tablename__ = "guest_permits"
    __table_args__ = (Index("ix_guest_permits_person", "person_id"),)

    person_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Unicode(300), default=None)


class Block(ReferenceMixin, Base):
    """Blocklist entry (مسدودی) for one plate or one person — never a whole shop."""

    __tablename__ = "blocks"
    __table_args__ = (Index("ix_blocks_plate", "plate_key"), Index("ix_blocks_person", "person_id"))

    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    person_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), default=None)
    category: Mapped[str] = mapped_column(String(20))  # debtor | security | harassment | other
    description: Mapped[str] = mapped_column(Unicode(500), default="")


class BlockAttempt(EventMixin, Base):
    __tablename__ = "block_attempts"
    __table_args__ = (Index("ix_block_attempts_block", "block_id"),)

    block_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    plate_key: Mapped[str | None] = mapped_column(Unicode(PLATE_KEY_LENGTH), default=None)
    gate_code: Mapped[int | None] = mapped_column(Integer, default=None)
    photo_path: Mapped[str | None] = mapped_column(Unicode(400), default=None)
    details: Mapped[dict[str, Any]] = mapped_column(JSONText(), default=dict)
