"""Report 20 (SPEC §4.12): ads, coupons, shop wallet statements — and the printable shop performance report."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import local_date
from caspian_parking.data.models import Ad, AdPrint, RaffleDraw, Shop, WalletTransaction
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, fa_money
from caspian_parking.services.coupons import shop_coupon_stats
from caspian_parking.services.exporters import Section
from caspian_parking.services.reports.base import ReportParams, ReportResult, jdate_text


def _prints_in_range(session: Session, params: ReportParams) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for ad_id, kind, count in session.execute(
        select(AdPrint.ad_id, AdPrint.kind, func.count())
        .where(AdPrint.created_at_utc >= params.start, AdPrint.created_at_utc < params.end)
        .group_by(AdPrint.ad_id, AdPrint.kind)
    ):
        counts[str(ad_id)][str(kind)] = int(count)
    return counts


def _ads_in_range(session: Session, params: ReportParams, shop_id: str | None = None) -> list[Ad]:
    first, last = local_date(params.start), local_date(params.end - timedelta(seconds=1))
    stmt = select(Ad).where(Ad.start_date <= last, Ad.end_date >= first).order_by(Ad.start_date)
    if shop_id:
        stmt = stmt.where(Ad.shop_id == shop_id)
    return list(session.scalars(stmt))


def ads(session: Session, params: ReportParams) -> ReportResult:
    names = {s.id: s.name for s in session.scalars(select(Shop))}
    prints = _prints_in_range(session, params)
    rows = []
    total_prints = 0
    total_price = 0
    for ad in _ads_in_range(session, params):
        entry, exit_ = prints[ad.id].get("entry", 0), prints[ad.id].get("exit", 0)
        total_prints += entry + exit_
        total_price += ad.price
        rows.append(
            [
                names.get(ad.shop_id, "—"),
                tr(f"ads.package.{ad.package}"),
                jdate_text(ad.start_date),
                jdate_text(ad.end_date),
                entry,
                exit_,
                ad.price,
            ]
        )
    return ReportResult(
        tr("report.ads"),
        [
            tr("ads.col_shop"),
            tr("ads.col_package"),
            tr("reports.from"),
            tr("reports.to"),
            tr("ads.col_prints_entry"),
            tr("ads.col_prints_exit"),
            tr("ads.col_price"),
        ],
        [Section("", rows)],
        kpis=[(tr("ads.kpi_ads"), fa_digits(len(rows))), (tr("ads.kpi_prints"), fa_digits(total_prints))],
        totals={"ads": len(rows), "prints": total_prints, "price": total_price},
        subtitle=params.label,
        widths=[24, 12, 14, 14, 12, 12, 16],
    )


def coupons(session: Session, params: ReportParams) -> ReportResult:
    stats = shop_coupon_stats(
        session, local_date(params.end - timedelta(seconds=1)), start=params.start, end=params.end
    )
    rows = [[s.shop_name, s.bought, s.used, s.expired, s.revenue] for s in stats]
    totals = {
        "bought": sum(s.bought for s in stats),
        "used": sum(s.used for s in stats),
        "expired": sum(s.expired for s in stats),
        "revenue": sum(s.revenue for s in stats),
    }
    if rows:
        rows.append([tr("fin.total"), totals["bought"], totals["used"], totals["expired"], totals["revenue"]])
    return ReportResult(
        tr("report.coupons"),
        [
            tr("ads.col_shop"),
            tr("coupons.bought"),
            tr("coupons.used_count"),
            tr("coupons.expired_count"),
            tr("coupons.revenue"),
        ],
        [Section("", rows)],
        kpis=[
            (tr("coupons.used_count"), fa_digits(totals["used"])),
            (tr("coupons.revenue"), fa_money(totals["revenue"])),
        ],
        totals=totals,
        subtitle=params.label,
        widths=[28, 14, 14, 14, 18],
    )


def wallets(session: Session, params: ReportParams) -> ReportResult:
    """Shop wallet statements: opening balance, deposits, debits, closing balance."""
    opening = dict(
        session.execute(
            select(WalletTransaction.shop_id, func.sum(WalletTransaction.amount))
            .where(WalletTransaction.created_at_utc < params.start)
            .group_by(WalletTransaction.shop_id)
        ).all()
    )
    moves: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for shop_id, amount in session.execute(
        select(WalletTransaction.shop_id, WalletTransaction.amount).where(
            WalletTransaction.created_at_utc >= params.start, WalletTransaction.created_at_utc < params.end
        )
    ):
        moves[shop_id][0 if amount > 0 else 1] += int(amount)
    rows = []
    for shop in session.scalars(select(Shop).order_by(Shop.name)):
        start_balance = int(opening.get(shop.id) or 0)
        deposits, debits = moves[shop.id]
        if not (start_balance or deposits or debits):
            continue
        rows.append([shop.name, start_balance, deposits, -debits, start_balance + deposits + debits])
    return ReportResult(
        tr("report.wallets"),
        [
            tr("ads.col_shop"),
            tr("wallet.opening"),
            tr("wallet.deposits"),
            tr("wallet.debits"),
            tr("wallet.closing"),
        ],
        [Section("", rows)],
        totals={"shops": len(rows), "closing": sum(int(r[4]) for r in rows)},  # type: ignore[call-overload]
        subtitle=params.label,
        widths=[28, 16, 16, 16, 16],
    )


def shop_performance(session: Session, params: ReportParams) -> ReportResult:
    """Printable one-shop report (text = shop name): ad prints, coupons, raffles sponsored."""
    needle = params.text.strip()
    shop = session.scalar(select(Shop).where(Shop.name.contains(needle)).order_by(Shop.name)) if needle else None
    if shop is None:
        return ReportResult(
            tr("report.shop_performance"), [tr("fin.col_item"), tr("rep.col_value")], subtitle=params.label
        )
    prints = _prints_in_range(session, params)
    ad_rows = []
    total_prints = 0
    for ad in _ads_in_range(session, params, shop.id):
        count = sum(prints[ad.id].values())
        total_prints += count
        label = f"{tr(f'ads.package.{ad.package}')} — {jdate_text(ad.start_date)} → {jdate_text(ad.end_date)}"
        ad_rows.append([label, fa_digits(count)])
    stats = shop_coupon_stats(
        session, local_date(params.end - timedelta(seconds=1)), shop_id=shop.id, start=params.start, end=params.end
    )
    coupon = stats[0] if stats else None
    coupon_rows = [
        [tr("coupons.bought"), fa_digits(coupon.bought if coupon else 0)],
        [tr("coupons.used_count"), fa_digits(coupon.used if coupon else 0)],
        [tr("coupons.expired_count"), fa_digits(coupon.expired if coupon else 0)],
        [tr("coupons.revenue"), fa_money(coupon.revenue if coupon else 0)],
    ]
    raffles = session.scalars(
        select(RaffleDraw).where(
            RaffleDraw.sponsor_shop_id == shop.id,
            RaffleDraw.created_at_utc >= params.start,
            RaffleDraw.created_at_utc < params.end,
        )
    ).all()
    raffle_rows = [[tr("raffle.month_label", month=fa_digits(r.month)), fa_digits(r.winner_ticket)] for r in raffles]
    sections = [Section(tr("ads.section_ads"), ad_rows), Section(tr("ads.section_coupons"), coupon_rows)]
    if raffle_rows:
        sections.append(Section(tr("ads.section_raffles"), raffle_rows))
    return ReportResult(
        tr("report.shop_performance_for", shop=shop.name),
        [tr("fin.col_item"), tr("rep.col_value")],
        sections,
        kpis=[
            (tr("ads.kpi_prints"), fa_digits(total_prints)),
            (tr("coupons.used_count"), fa_digits(coupon.used if coupon else 0)),
        ],
        totals={"prints": total_prints, "coupons_used": coupon.used if coupon else 0},
        subtitle=params.label,
        widths=[44, 24],
    )
