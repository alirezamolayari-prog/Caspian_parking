"""Gate operations for one gate PC (SPEC §4.2–4.4, §4.9, §4.10, §4.13 counters).

Every operation is one database transaction. Money and traffic facts are append-only events;
``active_sessions`` and ``visits`` are projections kept in step inside the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from caspian_parking.core.barcode import (
    SEQ_MODULO,
    BarcodeError,
    decode_payload,
    encode_payload,
    entry_minute,
    looks_like_payload,
)
from caspian_parking.core.jalali import local_date, local_day_range_utc, start_of_local_day_utc
from caspian_parking.core.money import require_int
from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import Plate, parse_plate
from caspian_parking.core.subscriptions import EntryStatus
from caspian_parking.core.tariff import PriceBreakdown, VehicleType, VisitKind
from caspian_parking.core.tickets import TicketNumber, TicketNumberError, parse_ticket_number
from caspian_parking.data.models import (
    ActiveSession,
    Adjustment,
    Cancellation,
    CouponRedemption,
    Debt,
    EntryEvent,
    ExitEvent,
    GateSequence,
    NightMark,
    Payment,
    Reprint,
    Visit,
)
from caspian_parking.data.models.people import Person
from caspian_parking.data.repositories.system import LevelRepository
from caspian_parking.services.blocklist import BlocklistService, BlockMatch
from caspian_parking.services.context import AppContext
from caspian_parking.services.coupons import CouponError, check_coupon
from caspian_parking.services.identification import Identification, Kind, identify
from caspian_parking.services.settings import get_setting
from caspian_parking.services.tariff_service import TariffContext, load_context

HMAC_SECRET = "ticket_hmac_key"


class PaymentMethod(StrEnum):
    CASH = "cash"
    CARD = "card"
    MALL_CARD = "mall_card"  # cash received, operator swiped the mall's own card


class Category(StrEnum):
    TRANSIENT = "transient"
    MOTORCYCLE = "motorcycle"
    PASS_THROUGH = "pass_through"
    SUBSCRIBER = "subscriber"
    FREE = "free"


class GateError(RuntimeError):
    """Operation refused; ``str(error)`` is an i18n key."""

    def __init__(self, key: str, **details: Any) -> None:
        super().__init__(key)
        self.details = details


class AlreadyInside(GateError):
    def __init__(self, session: ActiveSession) -> None:
        super().__init__("gate.already_inside", session=session)
        self.session = session


@dataclass(frozen=True)
class OpenDebt:
    debt: Debt
    remaining: int


class Blocked(GateError):
    """Entry refused: the plate (or its owner) is on the blocklist. The attempt has been logged."""

    def __init__(self, match: BlockMatch) -> None:
        super().__init__("gate.blocked", match=match)
        self.match = match


@dataclass(frozen=True)
class EntryResult:
    session: ActiveSession
    ticket: TicketNumber
    payload: str
    debts: list[OpenDebt] = field(default_factory=list)
    identification: Identification | None = None

    @property
    def needs_receipt(self) -> bool:
        """Covered visits (subscribers, free access) do not need a paper ticket."""
        return self.identification is None or not self.identification.covered


@dataclass(frozen=True)
class ExitQuote:
    session: ActiveSession
    exit_at: datetime
    breakdown: PriceBreakdown
    lost_ticket: bool = False
    coupon_id: str | None = None  # a scanned coupon (redeemed when the exit is completed)

    @property
    def amount_due(self) -> int:
        return self.breakdown.total


@dataclass(frozen=True)
class LevelOccupancy:
    code: str
    name: str
    capacity: int
    inside: int


def debt_paid(session: Session, debt_id: str) -> int:
    cancelled = select(Cancellation.target_id).where(Cancellation.target_table == "payments")
    paid = session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.debt_id == debt_id, Payment.id.not_in(cancelled)
        )
    )
    return int(paid or 0)


def open_debts(session: Session, plate_key: str) -> list[OpenDebt]:
    cancelled = select(Cancellation.target_id).where(Cancellation.target_table == "debts")
    debts = session.scalars(
        select(Debt).where(Debt.plate_key == plate_key, Debt.id.not_in(cancelled)).order_by(Debt.created_at_utc)
    ).all()
    result = []
    for debt in debts:
        remaining = debt.amount - debt_paid(session, debt.id)
        if remaining > 0:
            result.append(OpenDebt(debt, remaining))
    return result


def open_debts_total(session: Session, plate_key: str) -> int:
    return sum(d.remaining for d in open_debts(session, plate_key))


def category_for(vehicle: VehicleType, kind: VisitKind) -> Category:
    if kind is VisitKind.PASS_THROUGH:
        return Category.PASS_THROUGH
    if vehicle is VehicleType.MOTORCYCLE:
        return Category.MOTORCYCLE
    return Category.TRANSIENT


def breakdown_to_json(breakdown: PriceBreakdown) -> dict[str, Any]:
    return {
        "total_minutes": breakdown.total_minutes,
        "chargeable_minutes": breakdown.chargeable_minutes,
        "entry_fee": breakdown.entry_fee,
        "extra_minutes": breakdown.extra_minutes,
        "extra_amount": breakdown.extra_amount,
        "rounding": breakdown.rounding,
        "transient_fee": breakdown.transient_fee,
        "coupon_discount": breakdown.coupon_discount,
        "nights": breakdown.nights,
        "night_fine_each": breakdown.night_fine_each,
        "night_fines": breakdown.night_fines,
        "total": breakdown.total,
        "tariff_effective_from": breakdown.tariff_effective_from.isoformat(),
    }


class GateService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ---------------------------------------------------------------- basics
    @property
    def gate_code(self) -> int:
        code = self.ctx.config.gate_code
        if code is None:
            raise GateError("gate.no_gate_code")
        return code

    def hmac_key(self) -> bytes:
        return self.ctx.secrets.get_or_create(HMAC_SECRET)

    def _require(self, permission: str) -> None:
        if not self.ctx.can(permission):
            raise GateError("gate.permission_denied", permission=permission)

    def _now(self) -> datetime:
        return self.ctx.clock.now_utc()

    def tariffs(self) -> TariffContext:
        with self.ctx.read() as session:
            return load_context(session)

    def _next_sequence(self, session: Session) -> int:
        gate = self.gate_code
        row = session.get(GateSequence, gate)
        if row is None:
            row = GateSequence(gate_code=gate, last_sequence=0)
            session.add(row)
        issued = session.scalar(select(func.max(EntryEvent.ticket_sequence)).where(EntryEvent.gate_code == gate)) or 0
        row.last_sequence = max(row.last_sequence, int(issued)) + 1
        return row.last_sequence

    # ---------------------------------------------------------------- entry
    def register_entry(
        self,
        plate: Plate | None,
        vehicle: VehicleType = VehicleType.SEDAN,
        kind: VisitKind = VisitKind.TRANSIENT,
        pass_type: str | None = None,
    ) -> EntryResult:
        self._require(Permission.OPERATE_GATE)
        now = self._now()
        identification = self.identify(plate) if plate is not None else None
        if identification is not None and identification.block is not None:
            BlocklistService(self.ctx).record_attempt(
                identification.block.block, plate.key if plate else None, self.gate_code
            )
            raise Blocked(identification.block)
        category = category_for(vehicle, kind)
        person_id = None
        flags: list[str] = []
        if identification is not None and identification.person is not None:
            person_id = identification.person.id
            if identification.covered:
                category = Category.SUBSCRIBER if identification.kind is Kind.SUBSCRIBER else Category.FREE
                if identification.status is EntryStatus.NEGATIVE:
                    flags.append("negative_subscription")
            elif identification.concurrency_exceeded:
                flags.append("concurrency_exceeded")
            elif identification.kind is Kind.SUBSCRIBER:
                flags.append("expired_subscription")
        with self.ctx.uow() as session:
            debounce = int(get_setting(session, "gate.debounce_seconds"))
            if plate is not None:
                existing = session.scalar(select(ActiveSession).where(ActiveSession.plate_key == plate.key))
                if existing is not None:
                    session.expunge(existing)  # keep its values after the rollback
                    raise AlreadyInside(existing)
            else:
                recent = session.scalar(
                    select(func.count())
                    .select_from(ActiveSession)
                    .where(
                        ActiveSession.gate_code == self.gate_code,
                        ActiveSession.no_plate.is_(True),
                        ActiveSession.entry_at_utc > now - timedelta(seconds=debounce),
                    )
                )
                if recent:
                    raise GateError("gate.debounce")
            sequence = self._next_sequence(session)
            ticket = TicketNumber(self.gate_code, sequence)
            payload = encode_payload(self.gate_code, sequence, now, self.hmac_key())
            fields: dict[str, Any] = {
                "gate_code": self.gate_code,
                "ticket_sequence": sequence,
                "ticket_no": str(ticket),
                "plate_key": plate.key if plate else None,
                "vehicle_type": vehicle.value,
                "kind": kind.value,
                "pass_type": pass_type,
                "category": category.value,
                "entry_at_utc": now,
                "entry_minute": entry_minute(now),
                "no_plate": plate is None,
                "person_id": person_id,
            }
            active = ActiveSession(**fields, flags=flags)
            session.add(active)
            session.flush()
            session.add(EntryEvent(session_id=active.id, **fields))
            debts = self._open_debts(session, plate.key) if plate else []
        return EntryResult(active, ticket, payload, debts, identification)

    def identify(self, plate: Plate) -> Identification:
        with self.ctx.read() as session:
            return identify(self.ctx, session, plate.key, self._now())

    # ---------------------------------------------------------------- lookups
    def resolve(self, text: str) -> ActiveSession:
        """Find the vehicle for a scanned barcode, a typed ticket number or a plate."""
        text = text.strip()
        if not text:
            raise GateError("gate.ticket_not_found")
        with self.ctx.read() as session:
            if looks_like_payload(text):
                try:
                    payload = decode_payload(text, self.hmac_key())
                except BarcodeError as exc:
                    raise GateError("gate.ticket_invalid") from exc
                found = session.scalar(
                    select(ActiveSession).where(
                        ActiveSession.gate_code == payload.gate,
                        ActiveSession.entry_minute == payload.entry_minute,
                        ActiveSession.ticket_sequence % SEQ_MODULO == payload.sequence_mod,
                    )
                )
                if found is None:
                    raise GateError("gate.ticket_not_inside", payload=payload)
                return found
            try:
                ticket = parse_ticket_number(text)
            except TicketNumberError:
                ticket = None
            if ticket is not None:
                found = session.scalar(
                    select(ActiveSession).where(
                        ActiveSession.gate_code == ticket.gate, ActiveSession.ticket_sequence == ticket.sequence
                    )
                )
                if found is None:
                    raise GateError("gate.ticket_not_inside")
                return found
            try:
                plate = parse_plate(text)
            except ValueError as exc:
                raise GateError("gate.ticket_not_found") from exc
            found = session.scalar(select(ActiveSession).where(ActiveSession.plate_key == plate.key))
            if found is None:
                raise GateError("gate.ticket_not_found")
            return found

    def adopt_foreign_ticket(self, gate_code: int, sequence: int, entry_at: datetime) -> ActiveSession:
        """Exit of another gate's ticket while that entry has not synced yet (SPEC §2.3).

        The entry time comes from the signed barcode (or is typed from the printed ticket). A provisional
        session (flag ``foreign``) is used for the exit; sync links it to the real entry later (D-086).
        """
        self._require(Permission.OPERATE_GATE)
        if gate_code == self.gate_code:
            raise GateError("gate.ticket_not_inside")
        if entry_at > self._now():
            raise GateError("gate.bad_entry_time")
        ticket = TicketNumber(gate_code, sequence)
        with self.ctx.uow() as session:
            existing = session.scalar(
                select(ActiveSession).where(
                    ActiveSession.gate_code == gate_code, ActiveSession.ticket_no == str(ticket)
                )
            )
            if existing is not None:
                return existing
            active = ActiveSession(
                gate_code=gate_code,
                ticket_sequence=sequence,
                ticket_no=str(ticket),
                plate_key=None,
                vehicle_type=VehicleType.SEDAN.value,
                kind=VisitKind.TRANSIENT.value,
                pass_type=None,
                category=Category.TRANSIENT.value,
                entry_at_utc=entry_at,
                entry_minute=entry_minute(entry_at),
                no_plate=True,
                person_id=None,
                flags=["foreign"],
            )
            session.add(active)
        return active

    def get_active(self, session_id: str) -> ActiveSession:
        with self.ctx.read() as session:
            found = session.get(ActiveSession, session_id)
        if found is None:
            raise GateError("gate.not_inside")
        return found

    def search_inside(self, text: str = "", offset: int = 0, limit: int = 100) -> list[ActiveSession]:
        with self.ctx.read() as session:
            stmt = select(ActiveSession).order_by(ActiveSession.entry_at_utc.desc())
            if text.strip():
                like = f"%{text.strip()}%"
                stmt = stmt.where(or_(ActiveSession.plate_key.like(like), ActiveSession.ticket_no.like(like)))
            return list(session.scalars(stmt.offset(offset).limit(limit)))

    def todays_entries(self, offset: int = 0, limit: int = 100, inside_only: bool = False) -> list[EntryEvent]:
        start, end = local_day_range_utc(local_date(self._now()))
        with self.ctx.read() as session:
            stmt = (
                select(EntryEvent)
                .where(EntryEvent.entry_at_utc >= start, EntryEvent.entry_at_utc < end)
                .order_by(EntryEvent.entry_at_utc.desc())
            )
            if inside_only:
                stmt = stmt.where(EntryEvent.session_id.in_(select(ActiveSession.id)))
            return list(session.scalars(stmt.offset(offset).limit(limit)))

    def inside_count(self) -> int:
        with self.ctx.read() as session:
            return int(session.scalar(select(func.count()).select_from(ActiveSession)) or 0)

    # ---------------------------------------------------------------- exit
    def quote(
        self, session_id: str, lost_ticket: bool = False, coupon: bool = False, coupon_code: str | None = None
    ) -> ExitQuote:
        active = self.get_active(session_id)
        now = self._now()
        coupon_id = None
        if coupon_code:
            with self.ctx.read() as session:
                try:
                    coupon_id = check_coupon(session, coupon_code, local_date(now)).id
                except CouponError as exc:
                    raise GateError(str(exc)) from exc
            coupon = True
        covered = active.category in (Category.SUBSCRIBER.value, Category.FREE.value)
        night_exempt = False
        if active.person_id:
            with self.ctx.read() as session:
                person = session.get(Person, active.person_id)
                night_exempt = bool(person and person.night_exempt)
        breakdown = self.tariffs().quote(
            active.entry_at_utc,
            now,
            VehicleType(active.vehicle_type),
            VisitKind(active.kind),
            coupon=coupon,
            night_exempt=night_exempt,
            covered=covered,
        )
        return ExitQuote(active, now, breakdown, lost_ticket, coupon_id)

    def complete_exit(
        self,
        quote: ExitQuote,
        method: PaymentMethod | None,
        *,
        manual_amount: int | None = None,
        night_fines: int | None = None,
        reason: str | None = None,
        reference: str | None = None,
    ) -> Visit:
        """Record the exit and its payment. Manual amounts / night-fine changes need permission + reason."""
        self._require(Permission.OPERATE_GATE)
        breakdown = quote.breakdown
        amount_due = breakdown.total
        adjustments: list[tuple[str, int, int]] = []
        if night_fines is not None and night_fines != breakdown.night_fines:
            self._require(Permission.ADJUST_NIGHT_FINES)
            require_int(night_fines)
            if night_fines < 0 or night_fines > breakdown.night_fines:
                raise GateError("gate.bad_amount")
            new_due = breakdown.transient_fee + night_fines
            adjustments.append(("night_fine", amount_due, new_due))
            amount_due = new_due
        if manual_amount is not None and manual_amount != amount_due:
            self._require(Permission.ADJUST_AMOUNTS)
            require_int(manual_amount)
            if manual_amount < 0:
                raise GateError("gate.bad_amount")
            adjustments.append(("manual_amount", amount_due, manual_amount))
            amount_due = manual_amount
        if adjustments and not (reason and reason.strip()):
            raise GateError("gate.reason_required")
        if amount_due > 0 and method is None:
            raise GateError("gate.method_required")
        with self.ctx.uow(reason=reason) as session:
            active = session.get(ActiveSession, quote.session.id)
            if active is None:
                raise GateError("gate.not_inside")
            for kind, before, after in adjustments:
                session.add(
                    Adjustment(
                        session_id=active.id, kind=kind, amount_before=before, amount_after=after, reason=reason or ""
                    )
                )
            if quote.coupon_id is not None:
                if session.scalar(
                    select(func.count())
                    .select_from(CouponRedemption)
                    .where(CouponRedemption.coupon_id == quote.coupon_id)
                ):
                    raise GateError("coupons.used")
                session.add(
                    CouponRedemption(
                        coupon_id=quote.coupon_id, session_id=active.id, discount=breakdown.coupon_discount
                    )
                )
            status = "paid" if amount_due > 0 else "free"
            self._write_exit(session, active, quote, status, amount_due)
            if amount_due > 0 and method is not None:
                session.add(
                    Payment(
                        session_id=active.id,
                        purpose="parking",
                        method=method.value,
                        amount=amount_due,
                        gate_code=self.gate_code,
                        reference=reference,
                    )
                )
            return self._close_visit(session, active, quote, status, amount_due, paid=amount_due)

    def _write_exit(
        self, session: Session, active: ActiveSession, quote: ExitQuote, status: str, amount_due: int
    ) -> None:
        flags = sorted(quote.breakdown.flags | ({"lost_ticket"} if quote.lost_ticket else set()))
        session.add(
            ExitEvent(
                session_id=active.id,
                gate_code=self.gate_code,
                exit_at_utc=quote.exit_at,
                status=status,
                total_minutes=quote.breakdown.total_minutes,
                chargeable_minutes=quote.breakdown.chargeable_minutes,
                amount_due=amount_due,
                breakdown=breakdown_to_json(quote.breakdown),
                lost_ticket=quote.lost_ticket,
                flags=flags,
            )
        )

    def _close_visit(
        self, session: Session, active: ActiveSession, quote: ExitQuote | None, status: str, due: int, paid: int
    ) -> Visit:
        flags = set(active.flags or [])
        if quote is not None:
            flags |= quote.breakdown.flags
            if quote.lost_ticket:
                flags.add("lost_ticket")
        if active.night_marked:
            flags.add("night_marked")
        if active.duplicate_count:
            flags.add("duplicate")
        visit = Visit(
            session_id=active.id,
            plate_key=active.plate_key,
            ticket_no=active.ticket_no,
            gate_in=active.gate_code,
            gate_out=self.gate_code if quote is not None else None,
            vehicle_type=active.vehicle_type,
            kind=active.kind,
            category=active.category,
            entry_at_utc=active.entry_at_utc,
            exit_at_utc=quote.exit_at if quote is not None else None,
            total_minutes=quote.breakdown.total_minutes if quote is not None else 0,
            amount_due=due,
            amount_paid=paid,
            status=status,
            lost_ticket=bool(quote and quote.lost_ticket),
            no_plate=active.no_plate,
            night_count=quote.breakdown.nights if quote is not None else 0,
            person_id=active.person_id,
            flags=sorted(flags),
        )
        session.add(visit)
        session.delete(active)
        return visit

    # ---------------------------------------------------------------- fleeing & debts
    def flee(self, quote: ExitQuote, reason: str | None = None) -> Debt:
        """Exit without payment (فرار): the amount becomes a debt of the plate."""
        self._require(Permission.OPERATE_GATE)
        with self.ctx.uow(reason=reason) as session:
            active = session.get(ActiveSession, quote.session.id)
            if active is None:
                raise GateError("gate.not_inside")
            amount = quote.breakdown.total
            self._write_exit(session, active, quote, "fled", amount)
            debt = Debt(session_id=active.id, plate_key=active.plate_key, amount=amount, gate_code=self.gate_code)
            session.add(debt)
            self._close_visit(session, active, quote, "fled", amount, paid=0)
        return debt

    def _debt_paid(self, session: Session, debt_id: str) -> int:
        return debt_paid(session, debt_id)

    def _open_debts(self, session: Session, plate_key: str) -> list[OpenDebt]:
        return open_debts(session, plate_key)

    def open_debts(self, plate: Plate) -> list[OpenDebt]:
        with self.ctx.read() as session:
            return self._open_debts(session, plate.key)

    def collect_debt(self, debt_id: str, method: PaymentMethod, amount: int | None = None) -> Payment:
        self._require(Permission.OPERATE_GATE)
        with self.ctx.uow() as session:
            debt = session.get(Debt, debt_id)
            if debt is None:
                raise GateError("gate.debt_not_found")
            remaining = debt.amount - self._debt_paid(session, debt_id)
            if remaining <= 0:
                raise GateError("gate.debt_settled")
            pay = remaining if amount is None else require_int(amount)
            if pay <= 0 or pay > remaining:
                raise GateError("gate.bad_amount")
            payment = Payment(
                session_id=debt.session_id,
                debt_id=debt.id,
                purpose="debt",
                method=method.value,
                amount=pay,
                gate_code=self.gate_code,
            )
            session.add(payment)
            visit = session.scalar(select(Visit).where(Visit.session_id == debt.session_id))
            if visit is not None:
                visit.amount_paid += pay
                if pay == remaining:
                    visit.status = "recovered"
        return payment

    # ---------------------------------------------------------------- cancellations & corrections
    def cancel_entry(self, session_id: str, reason: str) -> Cancellation:
        self._require(Permission.CANCEL_TRANSACTIONS)
        if not reason.strip():
            raise GateError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            active = session.get(ActiveSession, session_id)
            if active is None:
                raise GateError("gate.not_inside")
            entry = session.scalar(select(EntryEvent).where(EntryEvent.session_id == session_id))
            cancellation = Cancellation(
                target_table="entry_events",
                target_id=entry.id if entry else session_id,
                session_id=session_id,
                reason=reason,
            )
            session.add(cancellation)
            self._close_visit(session, active, None, "cancelled", 0, paid=0)
        return cancellation

    def cancel_payment(self, payment_id: str, reason: str) -> Cancellation:
        """Void a wrongly recorded payment (e.g. cash recorded instead of card); record the right one after."""
        self._require(Permission.CANCEL_TRANSACTIONS)
        if not reason.strip():
            raise GateError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            payment = session.get(Payment, payment_id)
            if payment is None:
                raise GateError("gate.payment_not_found")
            already = session.scalar(
                select(func.count())
                .select_from(Cancellation)
                .where(Cancellation.target_table == "payments", Cancellation.target_id == payment_id)
            )
            if already:
                raise GateError("gate.already_cancelled")
            cancellation = Cancellation(
                target_table="payments", target_id=payment_id, session_id=payment.session_id, reason=reason
            )
            session.add(cancellation)
            if payment.session_id:
                visit = session.scalar(select(Visit).where(Visit.session_id == payment.session_id))
                if visit is not None:
                    visit.amount_paid -= payment.amount
                    visit.flags = sorted({*visit.flags, "payment_cancelled"})
        return cancellation

    def record_payment(self, session_id: str, method: PaymentMethod, amount: int, reason: str) -> Payment:
        """Record a (corrected) payment for a finished visit."""
        self._require(Permission.CANCEL_TRANSACTIONS)
        require_int(amount)
        with self.ctx.uow(reason=reason) as session:
            visit = session.scalar(select(Visit).where(Visit.session_id == session_id))
            if visit is None or amount <= 0:
                raise GateError("gate.bad_amount")
            payment = Payment(
                session_id=session_id, purpose="parking", method=method.value, amount=amount, gate_code=self.gate_code
            )
            session.add(payment)
            visit.amount_paid += amount
        return payment

    # ---------------------------------------------------------------- duplicates, night parking
    def reprint_duplicate(self, session_id: str) -> ActiveSession:
        self._require(Permission.OPERATE_GATE)
        with self.ctx.uow() as session:
            active = session.get(ActiveSession, session_id)
            if active is None:
                raise GateError("gate.not_inside")
            session.add(Reprint(session_id=session_id, gate_code=self.gate_code))
            active.duplicate_count += 1
            active.flags = sorted({*active.flags, "duplicate"})
        return active

    def mark_night(self, session_id: str) -> NightMark:
        self._require(Permission.OPERATE_GATE)
        with self.ctx.uow() as session:
            active = session.get(ActiveSession, session_id)
            if active is None:
                raise GateError("gate.not_inside")
            mark = NightMark(session_id=session_id, night_of=local_date(self._now()), auto=False)
            session.add(mark)
            active.night_marked = True
            active.flags = sorted({*active.flags, "night_parking"})
        return mark

    def last_closing_before(self, moment: datetime) -> datetime | None:
        calendar = self.tariffs().calendar
        day = local_date(moment)
        for offset in range(8):
            window = calendar.opening_utc(day - timedelta(days=offset))
            if window is not None and window[1] <= moment:
                return window[1]
        return None

    def auto_flag_overnight(self) -> list[str]:
        """Next morning: flag vehicles still inside since before the last closing, even if not marked."""
        now = self._now()
        closing = self.last_closing_before(now)
        if closing is None:
            return []
        flagged: list[str] = []
        with self.ctx.uow() as session:
            stale = session.scalars(select(ActiveSession).where(ActiveSession.entry_at_utc < closing)).all()
            for active in stale:
                night = local_date(closing)
                exists = session.scalar(
                    select(func.count())
                    .select_from(NightMark)
                    .where(NightMark.session_id == active.id, NightMark.night_of == night)
                )
                if exists:
                    continue
                session.add(NightMark(session_id=active.id, night_of=night, auto=True))
                active.night_marked = True
                active.flags = sorted({*active.flags, "night_parking", "overnight_auto"})
                flagged.append(active.id)
        return flagged

    # ---------------------------------------------------------------- occupancy & counters
    def occupancy(self) -> list[LevelOccupancy]:
        """Vehicles inside spread over the open parking levels in order (no per-level sensors)."""
        with self.ctx.read() as session:
            levels = [lv for lv in LevelRepository(session).active() if lv.is_parking and lv.is_open]
            inside = int(session.scalar(select(func.count()).select_from(ActiveSession)) or 0)
        result = []
        remaining = inside
        for index, level in enumerate(levels):
            last = index == len(levels) - 1
            here = remaining if last else min(remaining, level.capacity)
            result.append(LevelOccupancy(level.code, level.name, level.capacity, here))
            remaining -= here
        return result

    def fiscal_year_start(self) -> datetime:
        """Start of the open fiscal year (the immutable counters restart there)."""
        from caspian_parking.services.fiscal import ensure_fiscal_year, year_start_utc

        with self.ctx.uow() as session:
            return year_start_utc(ensure_fiscal_year(session, self._now()))

    def counters(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, int]:
        """Immutable per-category counters: derived from append-only entry events (nobody can edit them)."""
        start = since or self.fiscal_year_start()
        with self.ctx.read() as session:
            stmt = select(EntryEvent.category, func.count()).where(EntryEvent.entry_at_utc >= start)
            if until is not None:
                stmt = stmt.where(EntryEvent.entry_at_utc < until)
            rows = session.execute(stmt.group_by(EntryEvent.category)).all()
        counts = {c.value: 0 for c in Category}
        for category, count in rows:
            counts[str(category)] = int(count)
        counts["total"] = sum(counts[c.value] for c in Category)
        return counts

    def today_start(self) -> datetime:
        return start_of_local_day_utc(local_date(self._now()))

    def night_list(self) -> list[ActiveSession]:
        """Vehicles still inside — printed at closing for security."""
        return self.search_inside(limit=10_000)
