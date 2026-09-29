"""Subscribers, shops & wallets, free access (SPEC §4.6, §4.7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from caspian_parking.core.digits import normalize_input
from caspian_parking.core.jalali import local_date
from caspian_parking.core.money import require_int
from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import Plate
from caspian_parking.core.subscriptions import (
    Light,
    LightThresholds,
    Renewal,
    days_left,
    light_for,
    renew,
)
from caspian_parking.data.models import (
    EntryEvent,
    GuestPermit,
    Person,
    PersonPlate,
    Shop,
    SubscriptionPayment,
    WalletTransaction,
)
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.services.ads import advertiser_discount
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting
from caspian_parking.services.tariff_service import load_schedule

MOBILE_RE = re.compile(r"^09\d{9}$")


class PeopleError(RuntimeError):
    """``str(error)`` is an i18n key."""


class PersonRepository(ReferenceRepository[Person]):
    model = Person


class ShopRepository(ReferenceRepository[Shop]):
    model = Shop


class PlateRepository(ReferenceRepository[PersonPlate]):
    model = PersonPlate


def normalize_mobile(text: str | None) -> str | None:
    if not text:
        return None
    digits = "".join(ch for ch in normalize_input(text) if ch.isdigit())
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if not MOBILE_RE.match(digits):
        raise PeopleError("people.bad_mobile")
    return digits


@dataclass
class PersonInput:
    first_name: str
    last_name: str = ""
    mobile: str | None = None
    kind: str = "subscriber"
    free_category: str | None = None
    shop_id: str | None = None
    brand: str | None = None
    location_type: str = "inside"
    vehicle_model: str | None = None
    notes: str | None = None
    payer: str = "self"
    price_override: int | None = None
    max_concurrent: int = 1
    plates: list[tuple[Plate, str | None]] = field(default_factory=list)


@dataclass(frozen=True)
class SubscriptionState:
    person: Person
    light: Light
    days_left: int
    end: datetime | None


@dataclass(frozen=True)
class PaymentPreview:
    amount: int
    days: int
    renewal: Renewal


class PeopleService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def _require(self, permission: str) -> None:
        if not self.ctx.can(permission):
            raise PeopleError("gate.permission_denied")

    def _now(self) -> datetime:
        return self.ctx.clock.now_utc()

    def thresholds(self, session: Session) -> LightThresholds:
        return LightThresholds(
            int(get_setting(session, "subscription.amber_days")), int(get_setting(session, "subscription.red_hours"))
        )

    # ---------------------------------------------------------------- people & plates
    def create_person(self, data: PersonInput) -> Person:
        self._require(Permission.GRANT_GUEST if data.kind == "free" else Permission.MANAGE_SUBSCRIBERS)
        if not data.first_name.strip():
            raise PeopleError("people.name_required")
        mobile = normalize_mobile(data.mobile)
        if data.price_override is not None:
            require_int(data.price_override)
        with self.ctx.uow() as session:
            self._check_plates_free(session, [plate for plate, _ in data.plates])
            person = PersonRepository(session).add(
                Person(
                    kind=data.kind,
                    free_category=data.free_category,
                    first_name=data.first_name.strip(),
                    last_name=data.last_name.strip(),
                    mobile=mobile,
                    shop_id=data.shop_id,
                    brand=data.brand,
                    location_type=data.location_type,
                    vehicle_model=data.vehicle_model,
                    notes=data.notes,
                    payer=data.payer,
                    price_override=data.price_override,
                    max_concurrent=max(1, data.max_concurrent),
                    approved_by=self.ctx.user.id if data.kind == "free" and self.ctx.user else None,
                    negative_max_days=int(get_setting(session, "subscription.negative_max_days")),
                )
            )
            for plate, description in data.plates:
                PlateRepository(session).add(
                    PersonPlate(person_id=person.id, plate_key=plate.key, description=description)
                )
        return person

    def update_person(self, person_id: str, reason: str | None = None, **changes: Any) -> Person:
        self._require(Permission.MANAGE_SUBSCRIBERS)
        if "mobile" in changes:
            changes["mobile"] = normalize_mobile(changes["mobile"])
        blocked = {"subscription_end_utc", "negative_allowed", "night_exempt", "kind"}
        if blocked & set(changes):
            raise PeopleError("people.use_dedicated_action")
        with self.ctx.uow(reason=reason) as session:
            person = self._get(session, person_id)
            return PersonRepository(session).update(person, **changes)

    def _get(self, session: Session, person_id: str) -> Person:
        person = session.get(Person, person_id)
        if person is None:
            raise PeopleError("people.not_found")
        return person

    def get(self, person_id: str) -> Person:
        with self.ctx.read() as session:
            return self._get(session, person_id)

    def _check_plates_free(self, session: Session, plates: list[Plate], exclude_person: str | None = None) -> None:
        for plate in plates:
            stmt = select(PersonPlate).where(PersonPlate.plate_key == plate.key, PersonPlate.is_active.is_(True))
            owner = session.scalar(stmt)
            if owner is not None and owner.person_id != exclude_person:
                raise PeopleError("people.plate_taken")

    def add_plate(self, person_id: str, plate: Plate, description: str | None = None) -> PersonPlate:
        self._require(Permission.MANAGE_SUBSCRIBERS)
        with self.ctx.uow() as session:
            self._get(session, person_id)
            self._check_plates_free(session, [plate], exclude_person=person_id)
            existing = session.scalar(
                select(PersonPlate).where(PersonPlate.person_id == person_id, PersonPlate.plate_key == plate.key)
            )
            if existing is not None:
                if not existing.is_active:
                    PlateRepository(session).reactivate(existing, "plate re-added")
                return existing
            return PlateRepository(session).add(
                PersonPlate(person_id=person_id, plate_key=plate.key, description=description)
            )

    def remove_plate(self, plate_row_id: str, reason: str) -> None:
        self._require(Permission.MANAGE_SUBSCRIBERS)
        with self.ctx.uow() as session:
            row = session.get(PersonPlate, plate_row_id)
            if row is None:
                raise PeopleError("people.not_found")
            PlateRepository(session).deactivate(row, reason)

    def plates_of(self, person_id: str) -> list[PersonPlate]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(PersonPlate)
                    .where(PersonPlate.person_id == person_id, PersonPlate.is_active.is_(True))
                    .order_by(PersonPlate.created_at_utc)
                )
            )

    def person_for_plate(self, session: Session, plate_key: str) -> Person | None:
        row = session.scalar(
            select(PersonPlate).where(PersonPlate.plate_key == plate_key, PersonPlate.is_active.is_(True))
        )
        if row is None:
            return None
        person = session.get(Person, row.person_id)
        return person if person is not None and person.is_active else None

    def search(
        self, text: str = "", kind: str | None = "subscriber", offset: int = 0, limit: int = 100
    ) -> list[Person]:
        with self.ctx.read() as session:
            stmt = select(Person).where(Person.is_active.is_(True)).order_by(Person.first_name, Person.last_name)
            if kind is not None:
                stmt = stmt.where(Person.kind == kind)
            query = normalize_input(text)
            if query:
                like = f"%{query}%"
                plate_owners = select(PersonPlate.person_id).where(PersonPlate.plate_key.like(like))
                shop_ids = select(Shop.id).where(Shop.name.like(like))
                stmt = stmt.where(
                    or_(
                        Person.first_name.like(like),
                        Person.last_name.like(like),
                        Person.mobile.like(like),
                        Person.brand.like(like),
                        Person.id.in_(plate_owners),
                        Person.shop_id.in_(shop_ids),
                    )
                )
            return list(session.scalars(stmt.offset(offset).limit(limit)))

    def state(self, person: Person) -> SubscriptionState:
        now = self._now()
        with self.ctx.read() as session:
            thresholds = self.thresholds(session)
        end = person.subscription_end_utc
        return SubscriptionState(person, light_for(end, now, thresholds), days_left(end, now), end)

    # ---------------------------------------------------------------- subscription payments
    def price_for(self, session: Session, person: Person) -> tuple[int, int]:
        now = self._now()
        values = load_schedule(session).at(now)
        if person.price_override is not None:
            return person.price_override, values.subscription_days
        price = values.subscription_price
        discount = advertiser_discount(session, person.shop_id, local_date(now))
        return price - price * discount // 100, values.subscription_days

    def _entries_while_overdue(self, session: Session, person: Person, until: datetime) -> list[datetime]:
        if person.subscription_end_utc is None:
            return []
        rows = session.scalars(
            select(EntryEvent.entry_at_utc).where(
                EntryEvent.person_id == person.id,
                EntryEvent.category == "subscriber",  # entries paid as transient are not deducted
                EntryEvent.entry_at_utc > person.subscription_end_utc,
                EntryEvent.entry_at_utc <= until,
            )
        )
        return list(rows)

    def preview_payment(self, person_id: str) -> PaymentPreview:
        now = self._now()
        with self.ctx.read() as session:
            person = self._get(session, person_id)
            amount, days = self.price_for(session, person)
            renewal = renew(person.subscription_end_utc, now, days, self._entries_while_overdue(session, person, now))
        return PaymentPreview(amount, days, renewal)

    def pay_subscription(self, person_id: str, method: str, amount: int | None = None) -> SubscriptionPayment:
        """Record a subscription payment (cash / card / mall_card / wallet) and extend the end date."""
        self._require(Permission.MANAGE_SUBSCRIBERS)
        with self.ctx.uow() as session:
            return self._pay(session, person_id, method, amount)

    def _pay(self, session: Session, person_id: str, method: str, amount: int | None) -> SubscriptionPayment:
        now = self._now()
        person = self._get(session, person_id)
        price, days = self.price_for(session, person)
        paid = price if amount is None else require_int(amount)
        if paid < 0:
            raise PeopleError("gate.bad_amount")
        renewal = renew(person.subscription_end_utc, now, days, self._entries_while_overdue(session, person, now))
        shop_id = None
        if method == "wallet":
            if not person.shop_id:
                raise PeopleError("people.no_shop")
            shop_id = person.shop_id
        payment = SubscriptionPayment(
            person_id=person.id,
            amount=paid,
            method=method,
            shop_id=shop_id,
            paid_at_utc=now,
            period_days=days,
            previous_end_utc=person.subscription_end_utc,
            new_end_utc=renewal.new_end,
            used_days=[d.isoformat() for d in renewal.used_days],
            gate_code=self.ctx.config.gate_code,
        )
        session.add(payment)
        session.flush()
        if shop_id is not None:
            session.add(
                WalletTransaction(
                    shop_id=shop_id,
                    kind="subscription",
                    amount=-paid,
                    person_id=person.id,
                    reference_id=payment.id,
                )
            )
        PersonRepository(session).update(
            person, subscription_end_utc=renewal.new_end, negative_allowed=False, negative_approved_by=None
        )
        return payment

    def payments_of(self, person_id: str) -> list[SubscriptionPayment]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(SubscriptionPayment)
                    .where(SubscriptionPayment.person_id == person_id)
                    .order_by(SubscriptionPayment.paid_at_utc.desc())
                )
            )

    def allow_negative(self, person_id: str, max_days: int, reason: str) -> Person:
        self._require(Permission.ALLOW_NEGATIVE_SUBSCRIPTION)
        if not reason.strip():
            raise PeopleError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            person = self._get(session, person_id)
            return PersonRepository(session).update(
                person,
                negative_allowed=True,
                negative_max_days=max(1, max_days),
                negative_approved_by=self.ctx.user.id if self.ctx.user else None,
            )

    def revoke_negative(self, person_id: str, reason: str) -> Person:
        self._require(Permission.ALLOW_NEGATIVE_SUBSCRIPTION)
        with self.ctx.uow(reason=reason) as session:
            person = self._get(session, person_id)
            return PersonRepository(session).update(person, negative_allowed=False, negative_approved_by=None)

    def set_night_exempt(self, person_id: str, exempt: bool, reason: str) -> Person:
        """Permanent night-fine exemption (e.g. mall guards); the approver is recorded."""
        self._require(Permission.ADJUST_NIGHT_FINES)
        if not reason.strip():
            raise PeopleError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            person = self._get(session, person_id)
            return PersonRepository(session).update(
                person,
                night_exempt=exempt,
                night_exempt_approved_by=self.ctx.user.id if exempt and self.ctx.user else None,
            )

    def deactivate_person(self, person_id: str, reason: str) -> None:
        self._require(Permission.MANAGE_SUBSCRIBERS)
        with self.ctx.uow(reason=reason) as session:
            person = self._get(session, person_id)
            PersonRepository(session).deactivate(person, reason)
            for plate in session.scalars(select(PersonPlate).where(PersonPlate.person_id == person_id)):
                if plate.is_active:
                    PlateRepository(session).deactivate(plate, reason)

    # ---------------------------------------------------------------- shops & wallets
    def create_shop(self, name: str, location_type: str = "inside", **fields: Any) -> Shop:
        self._require(Permission.MANAGE_SHOPS)
        if not name.strip():
            raise PeopleError("people.name_required")
        with self.ctx.uow() as session:
            return ShopRepository(session).add(Shop(name=name.strip(), location_type=location_type, **fields))

    def update_shop(self, shop_id: str, **changes: Any) -> Shop:
        self._require(Permission.MANAGE_SHOPS)
        with self.ctx.uow() as session:
            shop = session.get(Shop, shop_id)
            if shop is None:
                raise PeopleError("people.not_found")
            return ShopRepository(session).update(shop, **changes)

    def shops(self, text: str = "") -> list[Shop]:
        with self.ctx.read() as session:
            stmt = select(Shop).where(Shop.is_active.is_(True)).order_by(Shop.name)
            if text.strip():
                stmt = stmt.where(Shop.name.like(f"%{normalize_input(text)}%"))
            return list(session.scalars(stmt))

    def _balance(self, session: Session, shop_id: str) -> int:
        total = session.scalar(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(WalletTransaction.shop_id == shop_id)
        )
        return int(total or 0)

    def balance(self, shop_id: str) -> int:
        with self.ctx.read() as session:
            return self._balance(session, shop_id)

    def low_balance(self, shop: Shop) -> bool:
        """Yellow light on the shop: balance below the threshold or below the next due renewals."""
        with self.ctx.read() as session:
            balance = self._balance(session, shop.id)
            due = sum(
                self.price_for(session, member)[0]
                for member in session.scalars(
                    select(Person).where(Person.shop_id == shop.id, Person.payer == "shop", Person.is_active.is_(True))
                )
            )
        return balance < max(shop.low_balance_threshold, due)

    def deposit(self, shop_id: str, amount: int, method: str, note: str | None = None) -> WalletTransaction:
        self._require(Permission.MANAGE_SHOPS)
        require_int(amount)
        if amount <= 0:
            raise PeopleError("gate.bad_amount")
        with self.ctx.uow() as session:
            if session.get(Shop, shop_id) is None:
                raise PeopleError("people.not_found")
            entry = WalletTransaction(shop_id=shop_id, kind="deposit", amount=amount, method=method, note=note)
            session.add(entry)
        return entry

    def statement(self, shop_id: str) -> list[tuple[WalletTransaction, int]]:
        """Wallet statement rows with the running balance (oldest first)."""
        with self.ctx.read() as session:
            rows = session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.shop_id == shop_id)
                .order_by(WalletTransaction.created_at_utc, WalletTransaction.id)
            ).all()
        running = 0
        result = []
        for row in rows:
            running += row.amount
            result.append((row, running))
        return result

    def members(self, shop_id: str) -> list[Person]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(Person)
                    .where(Person.shop_id == shop_id, Person.is_active.is_(True))
                    .order_by(Person.first_name)
                )
            )

    def renew_due_from_wallets(self, system: bool = False) -> list[SubscriptionPayment]:
        """Shop-paid subscriptions that have ended are renewed from the shop wallet (when enough balance).

        ``system`` = called by the scheduler (no logged-in user).
        """
        if not system:
            self._require(Permission.MANAGE_SUBSCRIBERS)
        now = self._now()
        payments: list[SubscriptionPayment] = []
        with self.ctx.uow() as session:
            due = session.scalars(
                select(Person).where(
                    Person.kind == "subscriber",
                    Person.payer == "shop",
                    Person.shop_id.is_not(None),
                    Person.is_active.is_(True),
                    or_(Person.subscription_end_utc.is_(None), Person.subscription_end_utc <= now),
                )
            ).all()
            for person in due:
                price, _days = self.price_for(session, person)
                if person.shop_id is None or self._balance(session, person.shop_id) < price:
                    continue
                payments.append(self._pay(session, person.id, "wallet", price))
        return payments

    # ---------------------------------------------------------------- free access
    def add_guest_permit(
        self, person_id: str, valid_from: date, valid_to: date, note: str | None = None
    ) -> GuestPermit:
        self._require(Permission.GRANT_GUEST)
        if valid_to < valid_from:
            raise PeopleError("people.bad_range")
        with self.ctx.uow() as session:
            person = self._get(session, person_id)
            if person.kind != "free":
                raise PeopleError("people.not_free")
            permit = GuestPermit(person_id=person_id, valid_from=valid_from, valid_to=valid_to, note=note)
            session.add(permit)
        return permit

    def valid_guest_permit(self, session: Session, person_id: str, at: datetime) -> GuestPermit | None:
        today = local_date(at)
        return session.scalar(
            select(GuestPermit).where(
                GuestPermit.person_id == person_id,
                GuestPermit.is_active.is_(True),
                GuestPermit.valid_from <= today,
                GuestPermit.valid_to >= today,
            )
        )

    def permits_of(self, person_id: str) -> list[GuestPermit]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(GuestPermit)
                    .where(GuestPermit.person_id == person_id)
                    .order_by(GuestPermit.valid_from.desc())
                )
            )
