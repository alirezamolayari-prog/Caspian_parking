"""Who is this plate? Subscriber / free access / blocked / debtor (SPEC §4.2 banners, §4.6–4.9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.subscriptions import EntryStatus, Light, days_left, entry_status, light_for
from caspian_parking.data.models import ActiveSession, Person, Shop
from caspian_parking.services.blocklist import BlocklistService, BlockMatch
from caspian_parking.services.context import AppContext
from caspian_parking.services.people import PeopleService


class Kind(StrEnum):
    UNKNOWN = "unknown"  # transient
    SUBSCRIBER = "subscriber"
    FREE = "free"


@dataclass(frozen=True)
class Identification:
    kind: Kind = Kind.UNKNOWN
    person: Person | None = None
    shop: Shop | None = None
    light: Light | None = None
    days_left: int = 0
    status: EntryStatus | None = None
    free_ok: bool = False
    concurrency_exceeded: bool = False
    block: BlockMatch | None = None
    debt_total: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        """The visit is paid by a subscription / free access (no parking fee)."""
        if self.concurrency_exceeded:
            return False
        if self.kind is Kind.SUBSCRIBER:
            return self.status in (EntryStatus.ACTIVE, EntryStatus.NEGATIVE)
        return self.kind is Kind.FREE and self.free_ok

    @property
    def blocked(self) -> bool:
        return self.block is not None


def identify(ctx: AppContext, session: Session, plate_key: str, now: datetime) -> Identification:
    from caspian_parking.services.gate_service import open_debts_total  # gate_service imports this module

    people = PeopleService(ctx)
    block = BlocklistService(ctx).match(session, plate_key)
    debt = open_debts_total(session, plate_key)
    person = people.person_for_plate(session, plate_key)
    if person is None:
        return Identification(block=block, debt_total=debt)
    shop = session.get(Shop, person.shop_id) if person.shop_id else None
    inside = int(
        session.scalar(select(func.count()).select_from(ActiveSession).where(ActiveSession.person_id == person.id)) or 0
    )
    exceeded = inside >= max(1, person.max_concurrent)
    if person.kind == "free":
        permanent = person.free_category in ("staff", "owner")
        free_ok = permanent or people.valid_guest_permit(session, person.id, now) is not None
        return Identification(
            Kind.FREE,
            person,
            shop,
            free_ok=free_ok,
            concurrency_exceeded=exceeded,
            block=block,
            debt_total=debt,
        )
    end = person.subscription_end_utc
    thresholds = people.thresholds(session)
    return Identification(
        Kind.SUBSCRIBER,
        person,
        shop,
        light=light_for(end, now, thresholds),
        days_left=days_left(end, now),
        status=entry_status(end, now, person.negative_allowed, person.negative_max_days),
        concurrency_exceeded=exceeded,
        block=block,
        debt_total=debt,
    )


__all__ = ["Identification", "Kind", "identify"]
