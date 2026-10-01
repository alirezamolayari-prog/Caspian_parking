"""Advertising (SPEC §4.11): ad contracts, rotation on receipts, print counts, calendar, raffle,
advertiser discount."""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import (
    JalaliDate,
    jalali_month_length,
    jalali_month_range_utc,
    local_date,
    start_of_local_day_utc,
)
from caspian_parking.core.money import require_int
from caspian_parking.core.permissions import Permission
from caspian_parking.core.receipt import ReceiptAd
from caspian_parking.core.subscriptions import Light, light_for
from caspian_parking.data.models import Ad, AdPrint, Cancellation, EntryEvent, Payment, RaffleDraw, Shop
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.data.repositories.system import SettingsRepository
from caspian_parking.data.repositories.tariff import KEY_FREE_WEEKDAYS, HolidayRepository
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting

PACKAGES = ("bronze", "silver", "gold")
PLACEMENTS = ("entry", "exit")


class AdError(RuntimeError):
    pass


class AdRepository(ReferenceRepository[Ad]):
    model = Ad


@dataclass(frozen=True)
class CalendarDay:
    day: date
    sold: int
    free: int
    weekend: bool
    occasion: str | None


def package_features(session: Session, package: str) -> list[str]:
    packages = get_setting(session, "ads.packages") or {}
    return list((packages.get(package) or {}).get("features", []))


def ad_end_utc(ad: Ad) -> datetime:
    return start_of_local_day_utc(ad.end_date + timedelta(days=1))


def ad_light(ad: Ad, now: datetime) -> Light:
    """Contract light, like subscriptions (green / amber / red / black)."""
    return light_for(ad_end_utc(ad), now)


class AdService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def _require(self) -> None:
        if not self.ctx.can(Permission.MANAGE_ADS):
            raise AdError("gate.permission_denied")

    # ---------------------------------------------------------------- contracts
    def create_ad(
        self,
        shop_id: str,
        package: str,
        start: date,
        end: date,
        text: str = "",
        offer: str = "",
        location: str = "",
        large: bool = False,
        price: int | None = None,
        weekdays: list[int] | None = None,
        on_entry: bool = True,
        on_exit: bool = False,
        logo_source: Path | None = None,
        method: str | None = None,
    ) -> Ad:
        """New ad contract. With ``method`` (cash / card / mall_card) the contract price is recorded as paid."""
        self._require()
        if package not in PACKAGES:
            raise AdError("ads.bad_package")
        if end < start:
            raise AdError("people.bad_range")
        if price is not None:
            require_int(price)
            if price < 0:
                raise AdError("gate.bad_amount")
        with self.ctx.uow() as session:
            if session.get(Shop, shop_id) is None:
                raise AdError("people.not_found")
            features = package_features(session, package)
            if price is None:
                price = int((get_setting(session, "ads.packages") or {}).get(package, {}).get("price", 0))
            logo_file = None
            if logo_source is not None:
                if "logo" not in features:
                    raise AdError("ads.logo_not_in_package")
                logo_file = self._copy_logo(logo_source)
            ad = AdRepository(session).add(
                Ad(
                    shop_id=shop_id,
                    package=package,
                    text=text.strip(),
                    offer=offer.strip(),
                    location=location.strip(),
                    large=large,
                    price=price,
                    weekdays=sorted(set(weekdays or [])),
                    on_entry=on_entry,
                    on_exit=on_exit,
                    start_date=start,
                    end_date=end,
                    logo_file=logo_file,
                )
            )
            if method is not None and price > 0:
                session.add(
                    Payment(
                        purpose="ad", method=method, amount=price, gate_code=self.ctx.config.gate_code, reference=ad.id
                    )
                )
            return ad

    def _copy_logo(self, source: Path) -> str:
        folder = self.ctx.data_root.ads / "logos"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        target.write_bytes(source.read_bytes())
        return f"logos/{source.name}"

    def update_ad(self, ad_id: str, **changes: Any) -> Ad:
        self._require()
        with self.ctx.uow() as session:
            ad = session.get(Ad, ad_id)
            if ad is None:
                raise AdError("people.not_found")
            return AdRepository(session).update(ad, **changes)

    def end_ad(self, ad_id: str, reason: str) -> None:
        self._require()
        with self.ctx.uow(reason=reason) as session:
            ad = session.get(Ad, ad_id)
            if ad is None:
                raise AdError("people.not_found")
            AdRepository(session).deactivate(ad, reason)

    def all_ads(self, offset: int = 0, limit: int = 200) -> list[Ad]:
        with self.ctx.read() as session:
            stmt = select(Ad).order_by(Ad.end_date.desc()).offset(offset).limit(limit)
            return list(session.scalars(stmt))

    def active_ads(self, day: date, placement: str | None = None) -> list[Ad]:
        """Ads running on ``day`` (expired contracts drop out automatically)."""
        with self.ctx.read() as session:
            ads = session.scalars(
                select(Ad)
                .where(Ad.is_active.is_(True), Ad.start_date <= day, Ad.end_date >= day)
                .order_by(Ad.created_at_utc)
            ).all()
        result = []
        for ad in ads:
            if ad.weekdays and day.weekday() not in ad.weekdays:
                continue
            if placement == "entry" and not ad.on_entry:
                continue
            if placement == "exit" and not ad.on_exit:
                continue
            result.append(ad)
        return result

    # ---------------------------------------------------------------- rotation & prints
    def next_ad(self, placement: str) -> Ad | None:
        """Fair rotation: the running ad with the fewest prints today (ties: oldest contract first)."""
        now = self.ctx.clock.now_utc()
        today = local_date(now)
        ads = self.active_ads(today, placement)
        if not ads:
            return None
        start = start_of_local_day_utc(today)
        with self.ctx.read() as session:
            counts = Counter(
                dict(
                    session.execute(
                        select(AdPrint.ad_id, func.count())
                        .where(AdPrint.ad_id.in_([a.id for a in ads]), AdPrint.created_at_utc >= start)
                        .group_by(AdPrint.ad_id)
                    ).all()
                )
            )
        return min(ads, key=lambda a: (counts.get(a.id, 0), a.created_at_utc))

    def receipt_ad(self, ad: Ad) -> ReceiptAd:
        with self.ctx.read() as session:
            shop = session.get(Shop, ad.shop_id)
            features = package_features(session, ad.package)
        logo = self.ctx.data_root.ads / ad.logo_file if ad.logo_file and "logo" in features else None
        lines = [ad.text] if ad.text else []
        return ReceiptAd(
            shop_name=shop.name if shop else "",
            location=ad.location or (shop.unit or "" if shop else ""),
            offer=ad.offer or "\n".join(lines),
            logo_path=logo,
            large=ad.large,
        )

    def record_print(self, ad: Ad, kind: str, session_id: str | None = None) -> AdPrint:
        with self.ctx.uow() as session:
            entry = AdPrint(ad_id=ad.id, kind=kind, session_id=session_id, gate_code=self.ctx.config.gate_code)
            session.add(entry)
        return entry

    def print_counts(self, ad_ids: list[str] | None = None) -> dict[str, int]:
        with self.ctx.read() as session:
            stmt = select(AdPrint.ad_id, func.count()).group_by(AdPrint.ad_id)
            if ad_ids is not None:
                stmt = stmt.where(AdPrint.ad_id.in_(ad_ids))
            return {str(k): int(v) for k, v in session.execute(stmt)}

    # ---------------------------------------------------------------- calendar
    def calendar(self, year: int, month: int) -> list[CalendarDay]:
        with self.ctx.read() as session:
            slots = int(get_setting(session, "ads.slots_per_day"))
            occasions = list(get_setting(session, "ads.occasions") or [])
            free_weekdays = set(SettingsRepository(session).get(KEY_FREE_WEEKDAYS, []) or [])
            first = JalaliDate(year, month, 1).to_gregorian()
            last = first + timedelta(days=jalali_month_length(year, month) - 1)
            holidays = {h.day: h.title for h in HolidayRepository(session).upcoming(first) if h.day <= last}
        days = []
        for offset in range(jalali_month_length(year, month)):
            day = first + timedelta(days=offset)
            sold = len(self.active_ads(day))
            jalali_day = offset + 1
            occasion = next(
                (o["title"] for o in occasions if o["month"] == month and o["from"] <= jalali_day <= o["to"]),
                holidays.get(day),
            )
            weekend = day.weekday() in free_weekdays or day in holidays
            days.append(CalendarDay(day, sold, max(0, slots - sold), weekend, occasion))
        return days

    # ---------------------------------------------------------------- raffle
    def draw_raffle(self, year: int, month: int, sponsor_shop_id: str | None = None) -> RaffleDraw:
        """Draw a winner among the month's transient receipts (secrets.SystemRandom), logged once per month."""
        self._require()
        key = f"{year:04d}-{month:02d}"
        start, end = jalali_month_range_utc(year, month)
        with self.ctx.read() as session:
            if session.scalar(select(func.count()).select_from(RaffleDraw).where(RaffleDraw.month == key)):
                raise AdError("raffle.already_drawn")
            candidates = session.execute(
                select(EntryEvent.ticket_no, EntryEvent.entry_at_utc)
                .where(
                    EntryEvent.category == "transient",
                    EntryEvent.entry_at_utc >= start,
                    EntryEvent.entry_at_utc < end,
                    EntryEvent.session_id.not_in(
                        select(Cancellation.session_id).where(Cancellation.session_id.is_not(None))
                    ),
                )
                .order_by(EntryEvent.entry_at_utc, EntryEvent.ticket_no)
            ).all()
        if not candidates:
            raise AdError("raffle.no_candidates")
        digest = hashlib.sha256("\n".join(t for t, _ in candidates).encode("utf-8")).hexdigest()
        ticket, entry_at = secrets.SystemRandom().choice(candidates)
        with self.ctx.uow() as session:
            draw = RaffleDraw(
                month=key,
                sponsor_shop_id=sponsor_shop_id,
                candidates=len(candidates),
                candidates_digest=digest,
                winner_ticket=ticket,
                winner_entry_at_utc=entry_at,
            )
            session.add(draw)
        return draw

    def raffles(self) -> list[RaffleDraw]:
        with self.ctx.read() as session:
            return list(session.scalars(select(RaffleDraw).order_by(RaffleDraw.month.desc())))


def advertiser_discount(session: Session, shop_id: str | None, day: date) -> int:
    """Percent discount on subscriptions for shops with a running ad (0 when none)."""
    if not shop_id:
        return 0
    running = session.scalar(
        select(func.count())
        .select_from(Ad)
        .where(Ad.shop_id == shop_id, Ad.is_active.is_(True), Ad.start_date <= day, Ad.end_date >= day)
    )
    return int(get_setting(session, "ads.advertiser_discount_percent")) if running else 0
