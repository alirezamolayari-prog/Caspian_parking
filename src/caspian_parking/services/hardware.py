"""Hardware rules (SPEC §6): when the barrier opens (and the log of it), RFID cards of subscribers /
free-access people (assign, lost card), card entry and exit at the gate, LED sign messages."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from caspian_parking.core.jalali import local_date
from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import Plate, PlateKind, plate_from_key
from caspian_parking.core.tariff import VehicleType
from caspian_parking.data.models import ActiveSession, Ad, BarrierOpen, Person, PersonCard, PersonPlate, Shop
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.devices.barrier import Barrier, BarrierError
from caspian_parking.devices.rfid import normalize_card
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_number
from caspian_parking.services.ads import package_features
from caspian_parking.services.context import AppContext
from caspian_parking.services.gate_service import GateError, GateService
from caspian_parking.services.settings import get_setting


class HardwareError(RuntimeError):
    pass


class CardRepository(ReferenceRepository[PersonCard]):
    model = PersonCard


# ---------------------------------------------------------------- barrier


class BarrierService:
    """Opens the lane's barrier and logs why (automatic causes or a manual reason)."""

    def __init__(self, ctx: AppContext, barrier: Barrier | None) -> None:
        self.ctx = ctx
        self.barrier = barrier

    @property
    def present(self) -> bool:
        return self.barrier is not None

    def open(self, lane: str, cause: str, session_id: str | None = None, reason: str | None = None) -> bool:
        if self.barrier is None:
            return False
        if cause == "manual":
            if not self.ctx.can(Permission.OPERATE_GATE):
                raise HardwareError("gate.permission_denied")
            if not (reason and reason.strip()):
                raise HardwareError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            session.add(
                BarrierOpen(
                    gate_code=self.ctx.config.gate_code,
                    lane=lane,
                    cause=cause,
                    session_id=session_id,
                    reason=reason.strip() if reason else None,
                )
            )
        try:
            self.barrier.open(lane)
        except BarrierError as exc:
            raise HardwareError("barrier.failed") from exc
        return True


# ---------------------------------------------------------------- cards


class CardService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def assign(self, person_id: str, uid: str, label: str = "") -> PersonCard:
        if not (self.ctx.can(Permission.MANAGE_SUBSCRIBERS) or self.ctx.can(Permission.GRANT_GUEST)):
            raise HardwareError("gate.permission_denied")
        card_uid = normalize_card(uid)
        if not card_uid:
            raise HardwareError("card.empty")
        with self.ctx.uow() as session:
            if session.get(Person, person_id) is None:
                raise HardwareError("people.not_found")
            existing = session.scalar(select(PersonCard).where(PersonCard.uid == card_uid))
            if existing is not None:
                raise HardwareError("card.taken" if existing.is_active else "card.was_lost")
            return CardRepository(session).add(PersonCard(person_id=person_id, uid=card_uid, label=label.strip()))

    def deactivate(self, card_id: str, reason: str) -> None:
        """Lost or returned card: it stops working immediately (the record stays for the history)."""
        if not (self.ctx.can(Permission.MANAGE_SUBSCRIBERS) or self.ctx.can(Permission.GRANT_GUEST)):
            raise HardwareError("gate.permission_denied")
        with self.ctx.uow(reason=reason) as session:
            card = session.get(PersonCard, card_id)
            if card is None:
                raise HardwareError("people.not_found")
            CardRepository(session).deactivate(card, reason)

    def cards_of(self, person_id: str) -> list[PersonCard]:
        with self.ctx.read() as session:
            return list(
                session.scalars(
                    select(PersonCard).where(PersonCard.person_id == person_id).order_by(PersonCard.created_at_utc)
                )
            )

    def person_for(self, uid: str) -> Person | None:
        with self.ctx.read() as session:
            card = session.scalar(
                select(PersonCard).where(PersonCard.uid == normalize_card(uid), PersonCard.is_active.is_(True))
            )
            return session.get(Person, card.person_id) if card is not None else None

    def is_card(self, text: str) -> bool:
        uid = normalize_card(text)
        if not uid:
            return False
        with self.ctx.read() as session:
            return session.scalar(select(PersonCard.id).where(PersonCard.uid == uid)) is not None


@dataclass(frozen=True)
class CardAction:
    """What a card at the gate means: the person's vehicle is inside → exit, otherwise → entry."""

    lane: str  # entry | exit
    person: Person
    plate: Plate | None = None
    vehicle: VehicleType = VehicleType.SEDAN
    session: ActiveSession | None = None


def resolve_card(ctx: AppContext, uid: str) -> CardAction:
    person = CardService(ctx).person_for(uid)
    if person is None:
        raise GateError("card.unknown")
    with ctx.read() as session:
        inside = session.scalar(
            select(ActiveSession).where(ActiveSession.person_id == person.id).order_by(ActiveSession.entry_at_utc)
        )
        plate_keys = list(
            session.scalars(
                select(PersonPlate.plate_key)
                .where(PersonPlate.person_id == person.id, PersonPlate.is_active.is_(True))
                .order_by(PersonPlate.created_at_utc)
            )
        )
    if inside is not None:
        return CardAction("exit", person, session=inside)
    if not plate_keys:
        raise GateError("card.no_plate")
    plate = plate_from_key(plate_keys[0])
    vehicle = VehicleType.MOTORCYCLE if plate.kind is PlateKind.MOTORCYCLE else VehicleType.SEDAN
    return CardAction("entry", person, plate=plate, vehicle=vehicle)


# ---------------------------------------------------------------- LED sign


def led_messages(ctx: AppContext) -> list[str]:
    """Free spaces per open parking level, then running ads of packages that include the LED sign."""
    messages = [
        tr("led.free_spaces", level=level.name, n=fa_number(max(0, level.capacity - level.inside)))
        for level in GateService(ctx).occupancy()
    ]
    today = local_date(ctx.clock.now_utc())
    with ctx.read() as session:
        ads = session.scalars(
            select(Ad).where(Ad.is_active.is_(True), Ad.start_date <= today, Ad.end_date >= today)
        ).all()
        for ad in ads:
            if "led" not in package_features(session, ad.package):
                continue
            shop = session.get(Shop, ad.shop_id)
            text = " — ".join(part for part in (shop.name if shop else "", ad.offer or ad.text) if part)
            if text:
                messages.append(text)
    return messages


def led_seconds(ctx: AppContext) -> int:
    with ctx.read() as session:
        return int(get_setting(session, "led.seconds"))
