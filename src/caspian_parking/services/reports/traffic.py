"""Traffic & people reports (SPEC §4.12: 2, 3, 4, 5, 6, 8, 9, 11, 15, 16, 21)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caspian_parking.core.jalali import local_date, start_of_local_day_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.subscriptions import Light, days_left, light_for
from caspian_parking.data.models import (
    ActiveSession,
    Block,
    BlockAttempt,
    Cancellation,
    EntryEvent,
    Person,
    PersonPlate,
    Reprint,
    SubscriptionPayment,
    Visit,
)
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime, fa_digits, fa_duration, fa_money
from caspian_parking.services.exporters import Section
from caspian_parking.services.reports.base import ReportParams, ReportResult, jdate_text
from caspian_parking.services.reports.common import plate_cell, user_names, visits_query
from caspian_parking.services.tariff_service import load_schedule

FREQUENT_MIN_VISITS = 4
FREQUENT_LIMIT = 50


def _visit_row(visit: Visit) -> list[object]:
    return [
        plate_cell(visit.plate_key),
        fa_datetime(visit.entry_at_utc),
        fa_datetime(visit.exit_at_utc) if visit.exit_at_utc else "—",
        fa_duration(visit.total_minutes),
        visit.amount_paid,
        tr(f"visit_status.{visit.status}"),
    ]


VISIT_COLUMNS_KEYS = (
    "rep.col_plate",
    "rep.col_entry",
    "rep.col_exit",
    "rep.col_duration",
    "rep.col_paid",
    "rep.col_status",
)
VISIT_WIDTHS = [24, 20, 20, 16, 16, 14]


def _visit_report(
    key: str, visits: list[Visit], params: ReportParams, extra_kpis: list[tuple[str, str]] | None = None
) -> ReportResult:
    paid = sum(v.amount_paid for v in visits)
    return ReportResult(
        tr(f"report.{key}"),
        [tr(k) for k in VISIT_COLUMNS_KEYS],
        [Section("", [_visit_row(v) for v in visits])],
        kpis=[(tr("rep.count"), fa_digits(len(visits))), (tr("rep.paid_total"), fa_money(paid)), *(extra_kpis or [])],
        totals={"count": len(visits), "paid": paid},
        subtitle=params.label,
        widths=VISIT_WIDTHS,
    )


def subscriptions(session: Session, params: ReportParams) -> ReportResult:
    now = params.end
    schedule = load_schedule(session)
    default_price = schedule.at(now).subscription_price
    people = session.scalars(select(Person).where(Person.kind == "subscriber", Person.is_active.is_(True))).all()
    sections: dict[Light, list[list[object]]] = {light: [] for light in Light}
    due = 0
    inside_mall = outside_mall = negative = 0
    for person in people:
        light = light_for(person.subscription_end_utc, now)
        price = person.price_override if person.price_override is not None else default_price
        if light is not Light.GREEN:
            due += price
        if person.location_type == "outside":
            outside_mall += 1
        else:
            inside_mall += 1
        negative += int(person.negative_allowed)
        sections[light].append(
            [
                person.full_name,
                person.brand or "",
                jdate_text(person.subscription_end_utc),
                fa_digits(days_left(person.subscription_end_utc, now)),
                tr(f"subs.location_{person.location_type}"),
                price,
            ]
        )
    collected = int(
        session.scalar(
            select(func.coalesce(func.sum(SubscriptionPayment.amount), 0)).where(
                SubscriptionPayment.paid_at_utc >= params.start,
                SubscriptionPayment.paid_at_utc < params.end,
                SubscriptionPayment.method != "import",
            )
        )
        or 0
    )
    order = (Light.GREEN, Light.AMBER, Light.RED, Light.BLACK)
    return ReportResult(
        tr("report.subscriptions"),
        [
            tr("follow.col_name"),
            tr("follow.col_shop"),
            tr("follow.col_end"),
            tr("follow.col_days"),
            tr("subs.location"),
            tr("rep.col_price"),
        ],
        [
            Section(tr(f"light.{light.value}") + f" ({fa_digits(len(sections[light]))})", sections[light])
            for light in order
        ],
        kpis=[
            (
                tr("subsrep.active"),
                fa_digits(len(sections[Light.GREEN]) + len(sections[Light.AMBER]) + len(sections[Light.RED])),
            ),
            (tr("subsrep.expired"), fa_digits(len(sections[Light.BLACK]))),
            (tr("subsrep.negative"), fa_digits(negative)),
            (tr("subsrep.collected"), fa_money(collected)),
            (tr("subsrep.due"), fa_money(due)),
            (tr("subsrep.inside_outside"), f"{fa_digits(inside_mall)} / {fa_digits(outside_mall)}"),
        ],
        totals={"collected": collected, "due": due, "people": len(people), "expired": len(sections[Light.BLACK])},
        subtitle=params.label,
        widths=[22, 20, 14, 12, 14, 16],
    )


def subscriber_traffic(session: Session, params: ReportParams) -> ReportResult:
    entries = session.execute(
        select(EntryEvent.person_id, EntryEvent.entry_at_utc)
        .where(
            EntryEvent.person_id.is_not(None),
            EntryEvent.category == "subscriber",
            EntryEvent.entry_at_utc >= params.start,
            EntryEvent.entry_at_utc < params.end,
        )
        .order_by(EntryEvent.entry_at_utc)
    ).all()
    by_person: dict[str, list[datetime]] = defaultdict(list)
    for person_id, at in entries:
        by_person[str(person_id)].append(at)
    names = {p.id: p.full_name for p in session.scalars(select(Person).where(Person.id.in_(list(by_person))))}
    rows = [
        [
            names.get(pid, "—"),
            fa_digits(len({local_date(t) for t in times})),
            fa_digits(len(times)),
            fa_datetime(times[0]),
            fa_datetime(times[-1]),
        ]
        for pid, times in sorted(by_person.items(), key=lambda item: -len(item[1]))
    ]
    return ReportResult(
        tr("report.subscriber_traffic"),
        [tr("follow.col_name"), tr("subtr.days"), tr("subtr.entries"), tr("subtr.first"), tr("subtr.last")],
        [Section("", rows)],
        kpis=[(tr("subtr.people"), fa_digits(len(rows))), (tr("subtr.entries"), fa_digits(len(entries)))],
        totals={"people": len(rows), "entries": len(entries)},
        subtitle=params.label,
        widths=[24, 12, 12, 20, 20],
    )


def plate_history(session: Session, params: ReportParams) -> ReportResult:
    sections: list[Section] = []
    kpis: list[tuple[str, str]] = []
    totals: dict[str, int] = {}
    text = params.text.strip()
    if text:
        try:
            key = parse_plate(text).key
        except ValueError:
            key = text
        visits = session.scalars(select(Visit).where(Visit.plate_key == key).order_by(Visit.entry_at_utc.desc())).all()
        minutes = sum(v.total_minutes for v in visits)
        paid = sum(v.amount_paid for v in visits)
        sections.append(Section(tr("plate.section_history", plate=plate_cell(key)), [_visit_row(v) for v in visits]))
        kpis += [
            (tr("rep.count"), fa_digits(len(visits))),
            (tr("rep.col_duration"), fa_duration(minutes)),
            (tr("rep.paid_total"), fa_money(paid)),
        ]
        totals.update(visits=len(visits), paid=paid)
    subscriber_plates = select(PersonPlate.plate_key).where(PersonPlate.is_active.is_(True))
    frequent = session.execute(
        select(Visit.plate_key, func.count(), func.sum(Visit.amount_paid))
        .where(
            Visit.entry_at_utc >= params.start,
            Visit.entry_at_utc < params.end,
            Visit.plate_key.is_not(None),
            Visit.plate_key.not_in(subscriber_plates),
        )
        .group_by(Visit.plate_key)
        .having(func.count() >= FREQUENT_MIN_VISITS)
        .order_by(func.count().desc())
        .limit(FREQUENT_LIMIT)
    ).all()
    sections.append(
        Section(
            tr("plate.section_frequent"),
            [[plate_cell(k), "", "", fa_digits(int(c)), int(p or 0), ""] for k, c, p in frequent],
        )
    )
    totals["frequent"] = len(frequent)
    return ReportResult(
        tr("report.plate_history"),
        [tr(k) for k in VISIT_COLUMNS_KEYS],
        sections,
        kpis=kpis,
        totals=totals,
        subtitle=params.label,
        widths=VISIT_WIDTHS,
    )


def free_access(session: Session, params: ReportParams) -> ReportResult:
    rows = []
    names = user_names(session)
    stmt = (
        select(EntryEvent, Person)
        .join(Person, Person.id == EntryEvent.person_id)
        .where(
            EntryEvent.category == "free", EntryEvent.entry_at_utc >= params.start, EntryEvent.entry_at_utc < params.end
        )
        .order_by(EntryEvent.entry_at_utc)
    )
    counts: dict[str, int] = defaultdict(int)
    for entry, person in session.execute(stmt):
        counts[person.free_category or ""] += 1
        rows.append(
            [
                person.full_name,
                tr(f"free.cat_{person.free_category}"),
                plate_cell(entry.plate_key),
                fa_datetime(entry.entry_at_utc),
                names.get(person.approved_by or "", "—"),
            ]
        )
    return ReportResult(
        tr("report.free_access"),
        [tr("follow.col_name"), tr("free.category"), tr("rep.col_plate"), tr("rep.col_entry"), tr("free.approver")],
        [Section("", rows)],
        kpis=[(tr(f"free.cat_{k}"), fa_digits(v)) for k, v in sorted(counts.items())],
        totals={"count": len(rows)},
        subtitle=params.label,
        widths=[22, 14, 24, 20, 18],
    )


def pass_through(session: Session, params: ReportParams) -> ReportResult:
    visits = session.scalars(
        visits_query(params).where(Visit.kind == "pass_through").order_by(Visit.entry_at_utc)
    ).all()
    types = {
        e.session_id: e.pass_type
        for e in session.scalars(select(EntryEvent).where(EntryEvent.session_id.in_([v.session_id for v in visits])))
    }
    names = user_names(session)
    by_type: dict[str, int] = defaultdict(int)
    over = 0
    rows = []
    for visit in visits:
        pass_type = types.get(visit.session_id) or "other"
        by_type[pass_type] += 1
        flagged = "pass_through_over_limit" in (visit.flags or [])
        over += int(flagged)
        rows.append(
            [
                plate_cell(visit.plate_key),
                tr(f"pass.{pass_type}"),
                fa_duration(visit.total_minutes),
                names.get(visit.created_by or "", "—"),
                visit.amount_paid,
                tr("pass.over") if flagged else "",
            ]
        )
    return ReportResult(
        tr("report.pass_through"),
        [
            tr("rep.col_plate"),
            tr("rep.col_kind"),
            tr("rep.col_duration"),
            tr("rep.col_user"),
            tr("rep.col_paid"),
            tr("rep.col_flag"),
        ],
        [Section("", rows)],
        kpis=[(tr(f"pass.{k}"), fa_digits(v)) for k, v in sorted(by_type.items())]
        + [(tr("pass.over"), fa_digits(over))],
        totals={"count": len(rows), "over_limit": over},
        subtitle=params.label,
        widths=[24, 16, 16, 18, 14, 14],
    )


def cancellations(session: Session, params: ReportParams) -> ReportResult:
    names = user_names(session)
    stmt = select(Cancellation).where(
        Cancellation.created_at_utc >= params.start, Cancellation.created_at_utc < params.end
    )
    if params.operator_id:
        stmt = stmt.where(Cancellation.created_by == params.operator_id)
    rows = [
        [fa_datetime(c.created_at_utc), names.get(c.created_by or "", "—"), tr(f"cancel.{c.target_table}"), c.reason]
        for c in session.scalars(stmt.order_by(Cancellation.created_at_utc))
    ]
    return ReportResult(
        tr("report.cancellations"),
        [tr("rep.col_when"), tr("rep.col_user"), tr("rep.col_kind"), tr("rep.col_reason")],
        [Section("", rows)],
        kpis=[(tr("rep.count"), fa_digits(len(rows)))],
        totals={"count": len(rows)},
        subtitle=params.label,
        widths=[20, 18, 18, 50],
    )


def lost_and_duplicates(session: Session, params: ReportParams) -> ReportResult:
    lost = session.scalars(visits_query(params).where(Visit.lost_ticket.is_(True))).all()
    reprints = session.scalars(
        select(Reprint).where(Reprint.created_at_utc >= params.start, Reprint.created_at_utc < params.end)
    ).all()
    return ReportResult(
        tr("report.lost"),
        [tr(k) for k in VISIT_COLUMNS_KEYS],
        [
            Section(tr("lost.section_lost"), [_visit_row(v) for v in lost]),
            Section(
                tr("lost.section_duplicates"),
                [["", fa_datetime(r.created_at_utc), "", "", "", tr("gate.mark_duplicate")] for r in reprints],
            ),
        ],
        kpis=[(tr("lost.lost"), fa_digits(len(lost))), (tr("lost.duplicates"), fa_digits(len(reprints)))],
        totals={"lost": len(lost), "duplicates": len(reprints)},
        subtitle=params.label,
        widths=VISIT_WIDTHS,
    )


def motorcycles(session: Session, params: ReportParams) -> ReportResult:
    visits = session.scalars(
        visits_query(params).where(Visit.vehicle_type == "motorcycle").order_by(Visit.entry_at_utc)
    ).all()
    return _visit_report("motorcycles", list(visits), params)


def no_plate(session: Session, params: ReportParams) -> ReportResult:
    visits = session.scalars(visits_query(params).where(Visit.no_plate.is_(True)).order_by(Visit.entry_at_utc)).all()
    return _visit_report("no_plate", list(visits), params)


def block_attempts(session: Session, params: ReportParams) -> ReportResult:
    rows = []
    stmt = (
        select(BlockAttempt, Block)
        .join(Block, Block.id == BlockAttempt.block_id)
        .where(BlockAttempt.created_at_utc >= params.start, BlockAttempt.created_at_utc < params.end)
        .order_by(BlockAttempt.created_at_utc)
    )
    for attempt, block in session.execute(stmt):
        rows.append(
            [
                plate_cell(attempt.plate_key),
                tr(f"block.cat_{block.category}"),
                fa_datetime(attempt.created_at_utc),
                fa_digits(attempt.gate_code or ""),
            ]
        )
    return ReportResult(
        tr("report.block_attempts"),
        [tr("rep.col_plate"), tr("block.category"), tr("rep.col_when"), tr("block.gate")],
        [Section("", rows)],
        kpis=[(tr("rep.count"), fa_digits(len(rows)))],
        totals={"count": len(rows)},
        subtitle=params.label,
        widths=[24, 16, 20, 10],
    )


def open_sessions(session: Session, params: ReportParams) -> ReportResult:
    """Vehicles still 'inside' that entered before today — e.g. left through the other gate unnoticed."""
    today_start = start_of_local_day_utc(local_date(params.end - timedelta(seconds=1)))
    rows = []
    old = 0
    for active in session.scalars(select(ActiveSession).order_by(ActiveSession.entry_at_utc)):
        stale = active.entry_at_utc < today_start
        old += int(stale)
        hours = int((params.end - active.entry_at_utc).total_seconds() // 3600)
        rows.append(
            [
                plate_cell(active.plate_key),
                fa_datetime(active.entry_at_utc),
                fa_digits(hours),
                active.ticket_no,
                tr("open.stale") if stale else "",
            ]
        )
    return ReportResult(
        tr("report.open_sessions"),
        [tr("rep.col_plate"), tr("rep.col_entry"), tr("open.hours"), tr("rep.col_ticket"), tr("rep.col_flag")],
        [Section("", rows)],
        kpis=[(tr("open.inside"), fa_digits(len(rows))), (tr("open.stale"), fa_digits(old))],
        totals={"inside": len(rows), "stale": old},
        subtitle=params.label,
        widths=[24, 20, 12, 16, 16],
    )


def outages(session: Session, params: ReportParams) -> ReportResult:
    """Power / PC outages detected from heartbeat gaps (SPEC §4.12 #19)."""
    from caspian_parking.data.models import Node, OutageEvent

    nodes = {n.id: n.name for n in session.scalars(select(Node))}
    rows = []
    total_minutes = 0
    for event in session.scalars(
        select(OutageEvent)
        .where(OutageEvent.started_at_utc >= params.start, OutageEvent.started_at_utc < params.end)
        .order_by(OutageEvent.started_at_utc)
    ):
        minutes = int((event.ended_at_utc - event.started_at_utc).total_seconds() // 60)
        total_minutes += minutes
        rows.append(
            [
                nodes.get(event.origin_node, event.origin_node[:8]),
                fa_datetime(event.started_at_utc),
                fa_datetime(event.ended_at_utc),
                fa_duration(minutes),
                tr("outage.clean") if event.clean_shutdown else tr("outage.power"),
            ]
        )
    return ReportResult(
        tr("report.outages"),
        [tr("outage.node"), tr("outage.from"), tr("outage.to"), tr("rep.col_duration"), tr("rep.col_kind")],
        [Section("", rows)],
        kpis=[(tr("rep.count"), fa_digits(len(rows))), (tr("outage.total"), fa_duration(total_minutes))],
        totals={"count": len(rows), "minutes": total_minutes},
        subtitle=params.label,
        widths=[18, 20, 20, 16, 18],
    )
