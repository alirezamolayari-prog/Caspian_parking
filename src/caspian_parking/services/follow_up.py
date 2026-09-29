"""Follow-up list (فهرست پیگیری, SPEC §4.6): black, red and amber subscribers, most urgent first."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from caspian_parking.core.jalali import format_jdate
from caspian_parking.core.plate import plate_from_key
from caspian_parking.core.subscriptions import LIGHT_URGENCY, Light, days_left, light_for
from caspian_parking.data.models import Person, PersonPlate, Shop
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits, fa_long_date
from caspian_parking.services.context import AppContext
from caspian_parking.services.exporters import ExportTable, Section, export_excel, export_word, plate_text
from caspian_parking.services.people import PeopleService

SECTIONS = (Light.BLACK, Light.RED, Light.AMBER)


@dataclass(frozen=True)
class FollowUpRow:
    person: Person
    shop_name: str
    plates: list[str]
    light: Light
    days_left: int
    end: datetime | None
    amount_due: int


def follow_up_rows(ctx: AppContext) -> list[FollowUpRow]:
    service = PeopleService(ctx)
    now = ctx.clock.now_utc()
    rows: list[FollowUpRow] = []
    with ctx.read() as session:
        thresholds = service.thresholds(session)
        people = session.scalars(select(Person).where(Person.kind == "subscriber", Person.is_active.is_(True))).all()
        shops = {s.id: s.name for s in session.scalars(select(Shop))}
        plates: dict[str, list[str]] = {}
        for plate in session.scalars(select(PersonPlate).where(PersonPlate.is_active.is_(True))):
            plates.setdefault(plate.person_id, []).append(plate.plate_key)
        for person in people:
            light = light_for(person.subscription_end_utc, now, thresholds)
            if light is Light.GREEN:
                continue
            price, _days = service.price_for(session, person)
            rows.append(
                FollowUpRow(
                    person,
                    shops.get(person.shop_id or "", person.brand or ""),
                    plates.get(person.id, []),
                    light,
                    days_left(person.subscription_end_utc, now),
                    person.subscription_end_utc,
                    price,
                )
            )
    rows.sort(key=lambda r: (LIGHT_URGENCY[r.light], r.days_left, r.person.full_name))
    return rows


def _plates_cell(keys: list[str]) -> str:
    texts = []
    for key in keys:
        try:
            texts.append(plate_text(plate_from_key(key)))
        except ValueError:
            texts.append(key)
    return "، ".join(texts)


def follow_up_table(ctx: AppContext, rows: list[FollowUpRow] | None = None) -> ExportTable:
    rows = follow_up_rows(ctx) if rows is None else rows
    columns = [tr(f"follow.col_{key}") for key in ("name", "shop", "phone", "plates", "end", "days", "due", "sign")]
    sections = []
    for light in SECTIONS:
        part = [r for r in rows if r.light is light]
        if not part:
            continue
        sections.append(
            Section(
                tr(f"follow.section_{light.value}", n=fa_digits(len(part))),
                [
                    [
                        r.person.full_name,
                        r.shop_name,
                        fa_digits(r.person.mobile or ""),
                        _plates_cell(r.plates),
                        fa_digits(format_jdate(r.end)) if r.end else "—",
                        fa_digits(r.days_left),
                        r.amount_due,
                        "",
                    ]
                    for r in part
                ],
            )
        )
    return ExportTable(
        tr("follow.title"),
        columns,
        sections,
        subtitle=fa_long_date(ctx.clock.now_utc()),
        widths=[22, 20, 16, 26, 14, 10, 14, 26],
    )


def export_follow_up(ctx: AppContext, folder: Path, kind: str) -> Path:
    table = follow_up_table(ctx)
    stamp = format_jdate(ctx.clock.now_utc(), sep="-")
    name = f"{tr('follow.file_name')}_{stamp}"
    if kind == "word":
        return export_word(table, folder / f"{name}.docx")
    return export_excel(table, folder / f"{name}.xlsx")
