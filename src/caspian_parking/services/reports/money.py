"""Money reports: financial (1), fleeing & debts (7), night parking & fines (10), manual changes (14)."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.data.models import Adjustment, Debt, ExitEvent, Gate, Payment, SubscriptionPayment, Visit
from caspian_parking.data.models.people import WalletTransaction
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_digits, fa_money
from caspian_parking.services.exporters import Section
from caspian_parking.services.reports.base import ReportParams, ReportResult
from caspian_parking.services.reports.common import not_cancelled, payments_query, plate_cell, user_names, visits_query

METHODS = ("cash", "card", "mall_card")
FIN_ROWS = ("parking", "debt", "subscription", "wallet", "coupon", "ad")


def _method_row(label: str, amounts: dict[str, int]) -> list[object]:
    values = [amounts.get(m, 0) for m in METHODS]
    return [label, *values, sum(values)]


def financial(session: Session, params: ReportParams) -> ReportResult:
    rows: dict[str, dict[str, int]] = defaultdict(dict)
    for purpose, method, total in session.execute(
        payments_query(params, Payment.purpose, Payment.method, func.sum(Payment.amount)).group_by(
            Payment.purpose, Payment.method
        )
    ):
        rows[str(purpose)][str(method)] = int(total or 0)
    subs_stmt = select(SubscriptionPayment.method, func.sum(SubscriptionPayment.amount)).where(
        SubscriptionPayment.paid_at_utc >= params.start,
        SubscriptionPayment.paid_at_utc < params.end,
        SubscriptionPayment.method.in_(METHODS),
    )
    if params.gate_code is not None:
        subs_stmt = subs_stmt.where(SubscriptionPayment.gate_code == params.gate_code)
    if params.operator_id:
        subs_stmt = subs_stmt.where(SubscriptionPayment.created_by == params.operator_id)
    for method, total in session.execute(subs_stmt.group_by(SubscriptionPayment.method)):
        rows["subscription"][str(method)] = int(total or 0)
    wallet_stmt = select(WalletTransaction.method, func.sum(WalletTransaction.amount)).where(
        WalletTransaction.kind == "deposit",
        WalletTransaction.created_at_utc >= params.start,
        WalletTransaction.created_at_utc < params.end,
    )
    if params.operator_id:
        wallet_stmt = wallet_stmt.where(WalletTransaction.created_by == params.operator_id)
    for method, total in session.execute(wallet_stmt.group_by(WalletTransaction.method)):
        rows["wallet"][str(method)] = int(total or 0)

    income = [_method_row(tr(f"fin.{key}"), rows.get(key, {})) for key in FIN_ROWS]
    totals = {m: sum(int(r[i + 1]) for r in income) for i, m in enumerate(METHODS)}  # type: ignore[call-overload]
    grand = sum(totals.values())
    income.append([tr("fin.total"), *[totals[m] for m in METHODS], grand])

    gate_names = {g.code: g.name for g in session.scalars(select(Gate))}
    by_gate = [
        [gate_names.get(code, str(code)), int(total or 0)]
        for code, total in session.execute(
            payments_query(params, Payment.gate_code, func.sum(Payment.amount)).group_by(Payment.gate_code)
        )
    ]
    names = user_names(session)
    by_operator = [
        [names.get(user or "", "—"), int(total or 0)]
        for user, total in session.execute(
            payments_query(params, Payment.created_by, func.sum(Payment.amount)).group_by(Payment.created_by)
        )
    ]
    paid_visits = int(
        session.scalar(
            select(func.count()).select_from(visits_query(params, by_exit=True).where(Visit.amount_paid > 0).subquery())
        )
        or 0
    )
    return ReportResult(
        tr("report.financial"),
        [tr("fin.col_item"), *[tr(f"payment.{m}") for m in METHODS], tr("fin.col_total")],
        [
            Section(tr("fin.section_income"), income),
            Section(tr("fin.section_gate"), [[g, "", "", "", t] for g, t in by_gate]),
            Section(tr("fin.section_operator"), [[o, "", "", "", t] for o, t in by_operator]),
        ],
        kpis=[(tr("fin.total"), fa_money(grand)), (tr("fin.paid_visits"), fa_digits(paid_visits))],
        totals={"grand": grand, **totals, "paid_visits": paid_visits},
        subtitle=params.label,
        widths=[26, 16, 16, 16, 18],
    )


def debts(session: Session, params: ReportParams) -> ReportResult:
    rows = []
    lost = recovered = 0
    stmt = select(Debt).where(
        Debt.created_at_utc >= params.start, Debt.created_at_utc < params.end, not_cancelled("debts", Debt.id)
    )
    if params.gate_code is not None:
        stmt = stmt.where(Debt.gate_code == params.gate_code)
    for debt in session.scalars(stmt.order_by(Debt.created_at_utc)):
        paid = int(
            session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.debt_id == debt.id, not_cancelled("payments", Payment.id)
                )
            )
            or 0
        )
        lost += debt.amount
        recovered += paid
        rows.append(
            [plate_cell(debt.plate_key), fa_datetime(debt.created_at_utc), debt.amount, paid, debt.amount - paid]
        )
    return ReportResult(
        tr("report.debts"),
        [
            tr("rep.col_plate"),
            tr("rep.col_when"),
            tr("debt.col_amount"),
            tr("debt.col_recovered"),
            tr("debt.col_remaining"),
        ],
        [Section("", rows)],
        kpis=[
            (tr("debt.count"), fa_digits(len(rows))),
            (tr("debt.lost"), fa_money(lost)),
            (tr("debt.recovered"), fa_money(recovered)),
        ],
        totals={"count": len(rows), "lost": lost, "recovered": recovered},
        subtitle=params.label,
        widths=[24, 20, 16, 16, 16],
    )


def night(session: Session, params: ReportParams) -> ReportResult:
    visits = session.scalars(visits_query(params, by_exit=True).where(Visit.night_count > 0)).all()
    exits = {
        e.session_id: e
        for e in session.scalars(select(ExitEvent).where(ExitEvent.session_id.in_([v.session_id for v in visits])))
    }
    rows = []
    fines = 0
    for visit in visits:
        event = exits.get(visit.session_id)
        amount = int(event.breakdown.get("night_fines", 0)) if event else 0
        fines += amount
        rows.append(
            [
                plate_cell(visit.plate_key),
                fa_datetime(visit.entry_at_utc),
                fa_datetime(visit.exit_at_utc) if visit.exit_at_utc else "",
                fa_digits(visit.night_count),
                amount,
            ]
        )
    names = user_names(session)
    waivers = [
        [names.get(a.created_by or "", "—"), fa_datetime(a.created_at_utc), a.amount_before, a.amount_after, a.reason]
        for a in session.scalars(
            select(Adjustment).where(
                Adjustment.kind == "night_fine",
                Adjustment.created_at_utc >= params.start,
                Adjustment.created_at_utc < params.end,
            )
        )
    ]
    return ReportResult(
        tr("report.night"),
        [tr("rep.col_plate"), tr("rep.col_entry"), tr("rep.col_exit"), tr("night.col_nights"), tr("night.col_fines")],
        [Section(tr("night.section_visits"), rows), Section(tr("night.section_waivers"), waivers)],
        kpis=[
            (tr("night.count"), fa_digits(len(rows))),
            (tr("night.fines"), fa_money(fines)),
            (tr("night.waivers"), fa_digits(len(waivers))),
        ],
        totals={"visits": len(rows), "fines": fines, "waivers": len(waivers)},
        subtitle=params.label,
        widths=[24, 20, 20, 12, 16],
    )


def manual_changes(session: Session, params: ReportParams) -> ReportResult:
    names = user_names(session)
    stmt = select(Adjustment).where(Adjustment.created_at_utc >= params.start, Adjustment.created_at_utc < params.end)
    if params.operator_id:
        stmt = stmt.where(Adjustment.created_by == params.operator_id)
    rows = [
        [
            fa_datetime(a.created_at_utc),
            names.get(a.created_by or "", "—"),
            tr(f"adjust.{a.kind}"),
            a.amount_before,
            a.amount_after,
            a.reason,
        ]
        for a in session.scalars(stmt.order_by(Adjustment.created_at_utc))
    ]
    difference = sum(int(r[4]) - int(r[3]) for r in rows)  # type: ignore[call-overload]
    return ReportResult(
        tr("report.manual_changes"),
        [
            tr("rep.col_when"),
            tr("rep.col_user"),
            tr("rep.col_kind"),
            tr("adjust.before"),
            tr("adjust.after"),
            tr("rep.col_reason"),
        ],
        [Section("", rows)],
        kpis=[(tr("adjust.count"), fa_digits(len(rows))), (tr("adjust.difference"), fa_money(difference))],
        totals={"count": len(rows), "difference": difference},
        subtitle=params.label,
        widths=[20, 18, 16, 16, 16, 40],
    )
