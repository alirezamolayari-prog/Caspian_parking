"""Phase 9: two gates + central database. Online, offline, reconnect, idempotent re-push, cross-gate tickets,
reference conflicts, duplicate payments → review queue, clock drift, joining."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import func, insert, select

from caspian_parking.config.machine import MachineConfig, Role, save_machine_config
from caspian_parking.config.paths import DataRoot
from caspian_parking.core.barcode import decode_payload
from caspian_parking.core.clock import FixedClock
from caspian_parking.core.jalali import local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.data.db import create_sqlite_engine
from caspian_parking.data.models import (
    ActiveSession,
    AuditLog,
    EntryEvent,
    Person,
    ReviewItem,
    SessionAlias,
    Shop,
    SyncLog,
    SyncOutbox,
    Visit,
)
from caspian_parking.data.repositories.system import UserRepository
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.context import open_context
from caspian_parking.services.gate_service import GateError, GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.sync import (
    GAP_GRACE,
    SyncEngine,
    SyncError,
    fetch_ticket_key,
    join_server,
)

MON = date(2026, 9, 28)
PLATE = parse_plate("12ب345-22")


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


class Site:
    """A central database plus gates, all on this PC (SQLite files or LocalDB)."""

    def __init__(self, tmp_path, server_engine, clock: FixedClock) -> None:
        self.tmp = tmp_path
        self.clock = clock
        self.server_engine = server_engine
        root = tmp_path / "server"
        save_machine_config(DataRoot(root).ensure().config, MachineConfig(role=Role.SERVER, gate_code=None))
        self.server = open_context(root, clock=clock, engine=server_engine)
        with self.server.uow() as session:
            user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
        self.server.user = CurrentUser.from_user(user)
        self.gates: dict[str, object] = {}

    def server_clock(self, _conn):
        return self.clock.now_utc()

    def gate(self, name: str, code: int):
        root = self.tmp / name
        save_machine_config(DataRoot(root).ensure().config, MachineConfig(gate_code=code, node_name=name))
        join_server(root, self.server_engine, self.clock, self.server_clock)
        ctx = open_context(root, clock=self.clock, server_engine=self.server_engine)
        with ctx.read() as session:
            user = UserRepository(session).by_username("boss")
        ctx.user = CurrentUser.from_user(user)
        self.gates[name] = ctx
        return ctx

    def sync(self, ctx, **kw):
        return SyncEngine(ctx, self.server_engine, self.server_clock).sync_once(**kw)

    def close(self):
        for ctx in self.gates.values():
            ctx.engine.dispose()
        self.server.engine.dispose()


@pytest.fixture
def site(tmp_path, clock):
    clock.set(at(MON, 10))
    engine = create_sqlite_engine(tmp_path / "central.db")
    result = Site(tmp_path, engine, clock)
    yield result
    result.close()


def count(ctx_or_engine, model, *where) -> int:
    engine = getattr(ctx_or_engine, "engine", ctx_or_engine)
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(model).where(*where)).scalar_one())


def test_join_takes_server_data_and_ticket_key(site):
    gate = site.gate("gate1", 1)
    assert gate.config.role is Role.GATE
    assert gate.config.joined_at
    from caspian_parking.services.gate_service import HMAC_SECRET

    assert gate.secrets.get(HMAC_SECRET) == fetch_ticket_key(site.server_engine)
    # the same reference rows (same ids) as the server, no second copy of the seeds
    with site.server.read() as s1, gate.read() as s2:
        server_shops = {u.id for u in UserRepository(s1).page(0, 50)}
        gate_users = {u.id for u in UserRepository(s2).page(0, 50)}
    assert server_shops == gate_users
    assert count(gate, EntryEvent) == 0
    status = site.sync(gate)
    assert status.online
    assert status.pending == 0
    assert count(site.server_engine, SyncLog, SyncLog.table_name == "nodes") >= 2


def test_entry_on_one_gate_exit_on_the_other(site, clock):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    entry = GateService(g1).register_entry(PLATE)
    assert count(g1, SyncOutbox) > 0
    site.sync(g1)
    assert count(g1, SyncOutbox) == 0
    site.sync(g2)
    gate2 = GateService(g2)
    found = gate2.resolve(entry.payload)  # scanned at the other gate
    assert found.id == entry.session.id
    clock.advance(minutes=50)
    visit = gate2.complete_exit(gate2.quote(found.id), PaymentMethod.CARD)
    assert visit.gate_out == 2
    site.sync(g2)
    site.sync(g1)
    assert count(g1, ActiveSession) == 0
    assert count(site.server_engine, Visit) == 1
    assert count(g1, Visit) == 1


def test_offline_then_reconnect_and_idempotent_repush(site, tmp_path):
    g1 = site.gate("gate1", 1)
    offline = SyncEngine(g1, create_sqlite_engine(tmp_path / "missing" / "nowhere" / "x.db"), site.server_clock)
    GateService(g1).register_entry(PLATE)
    status = offline.sync_once()
    assert not status.online
    assert status.error
    assert status.pending > 0
    queued = status.pending
    first = site.sync(g1)
    assert first.online
    assert first.pushed == queued
    before = (count(site.server_engine, EntryEvent), count(site.server_engine, SyncLog))
    # the same rows pushed again (e.g. power cut after the server committed) change nothing
    with g1.engine.begin() as conn:
        for (row_id,) in conn.execute(select(EntryEvent.id)):
            conn.execute(
                insert(SyncOutbox.__table__),
                [
                    {
                        "id": f"r-{row_id}",
                        "table_name": "entry_events",
                        "row_id": row_id,
                        "queued_at_utc": site.clock.now_utc(),
                    }
                ],
            )
    again = site.sync(g1)
    assert again.pushed == 0
    assert (count(site.server_engine, EntryEvent), count(site.server_engine, SyncLog)) == before


def test_reference_conflict_goes_to_review(site, clock):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    person = PeopleService(g1).create_person(PersonInput(first_name="علی", plates=[(PLATE, None)]))
    site.sync(g1)
    site.sync(g2)
    clock.advance(minutes=1)
    PeopleService(g1).update_person(person.id, notes="از درب ۱")
    clock.advance(minutes=1)
    PeopleService(g2).update_person(person.id, notes="از درب ۲")
    site.sync(g1)
    site.sync(g2)  # same version, later edit wins → gate 2's change; gate 1's change kept for review
    site.sync(g1)
    with site.server.read() as session:
        assert session.get(Person, person.id).notes == "از درب ۲"
        review = session.scalars(select(ReviewItem).where(ReviewItem.kind == "conflict")).one()
        assert review.refs["table"] == "people"
        lost = session.scalars(select(AuditLog).where(AuditLog.action == "sync_conflict")).one()
        assert lost.changes["lost"]["notes"] == "از درب ۱"
    with g1.read() as session:
        assert session.get(Person, person.id).notes == "از درب ۲"  # converged
        assert session.scalar(select(func.count()).select_from(ReviewItem)) == 1


def test_duplicate_subscription_payment_offline(site):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    person = PeopleService(g1).create_person(PersonInput(first_name="علی", plates=[(PLATE, None)]))
    site.sync(g1)
    site.sync(g2)
    # LAN cable down: the subscriber pays at both gates
    p1 = PeopleService(g1).pay_subscription(person.id, "cash")
    p2 = PeopleService(g2).pay_subscription(person.id, "cash")
    site.sync(g1)
    site.sync(g2)
    site.sync(g1)
    with g1.read() as session:
        item = session.scalars(select(ReviewItem).where(ReviewItem.kind == "duplicate_payment")).one()
    assert set(item.refs["ids"]) == {p1.id, p2.id}
    assert item.status == "open"
    # the supervisor cancels one; nothing was deleted automatically
    PeopleService(g1).cancel_subscription_payment(p2.id, "پرداخت تکراری")
    with g1.read() as session:
        assert session.get(Person, person.id).subscription_end_utc == p1.new_end_utc
    site.sync(g1)
    site.sync(g2)
    from caspian_parking.services.reports import run_report
    from caspian_parking.services.reports.base import range_params

    financial = run_report(site.server, "financial", range_params(MON, MON))
    from caspian_parking.i18n import tr

    rows = {r[0]: r[-1] for r in financial.sections[0].rows}
    assert rows[tr("fin.subscription")] == p1.amount


def test_cross_gate_ticket_while_offline(site, clock):
    g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
    entry = GateService(g1).register_entry(PLATE)  # not synced: link is down
    clock.advance(minutes=30)
    gate2 = GateService(g2)
    with pytest.raises(GateError) as error:
        gate2.resolve(entry.payload)
    payload = error.value.details["payload"]
    assert payload == decode_payload(entry.payload, gate2.hmac_key())
    provisional = gate2.adopt_foreign_ticket(payload.gate, payload.sequence_mod, payload.entry_utc)
    quote = gate2.quote(provisional.id)
    assert quote.breakdown.total_minutes == 30
    gate2.complete_exit(quote, PaymentMethod.CASH)
    # link back: gate 1's entry reaches gate 2, which links it to its provisional exit
    site.sync(g1)
    site.sync(g2)
    assert count(g2, ActiveSession) == 0
    assert count(g2, SessionAlias) == 1
    site.sync(g2)
    site.sync(g1)
    assert count(g1, ActiveSession) == 0  # gate 1 no longer shows the car inside
    with pytest.raises(GateError):
        gate2.adopt_foreign_ticket(2, 5, clock.now_utc())  # own gate's tickets are never adopted


def test_server_edits_reach_gates(site):
    g1 = site.gate("gate1", 1)
    shop = PeopleService(site.server).create_shop("مبلمان آرتا")
    site.sync(g1)
    with g1.read() as session:
        assert session.get(Shop, shop.id).name == "مبلمان آرتا"


def test_clock_drift_warning(site, clock):
    g1 = site.gate("gate1", 1)
    status = SyncEngine(g1, site.server_engine, lambda _c: clock.now_utc() + timedelta(minutes=5)).sync_once()
    assert status.drift_warning
    assert status.drift_seconds == pytest.approx(-300)
    assert not site.sync(g1).drift_warning


def test_pull_waits_for_uncommitted_sequence_numbers(site, clock):
    g1 = site.gate("gate1", 1)
    site.sync(g1)
    shop = PeopleService(site.server).create_shop("الف")
    engine = SyncEngine(g1, site.server_engine, site.server_clock)
    assert engine.pull() >= 1  # contiguous rows are applied normally
    with site.server_engine.begin() as conn:
        last = conn.execute(select(func.max(SyncLog.seq))).scalar_one()
        conn.execute(
            insert(SyncLog.__table__),
            [
                {
                    "seq": last + 3,
                    "table_name": "shops",
                    "row_id": shop.id,
                    "writer_node": "x",
                    "logged_at_utc": clock.now_utc(),
                }
            ],
        )
    assert engine.pull() == 0  # recent gap: wait
    with g1.engine.connect() as conn:
        from caspian_parking.services.sync import CURSOR_KEY, _state

        assert int(_state(conn, CURSOR_KEY)) == last
    clock.advance(seconds=GAP_GRACE.total_seconds() + 1)
    engine.pull()
    with g1.engine.connect() as conn:
        assert int(_state(conn, CURSOR_KEY)) == last + 3  # old gap = rolled-back transaction, skipped


def test_join_refuses_a_used_local_database(site, clock):
    root = site.tmp / "used"
    save_machine_config(DataRoot(root).ensure().config, MachineConfig(gate_code=3))
    ctx = open_context(root, clock=clock)
    with ctx.uow() as session:
        user = auth.create_user(session, "op", "Op", "secret1", preset="admin")
    ctx.user = CurrentUser.from_user(user)
    GateService(ctx).register_entry(PLATE)
    ctx.close()
    with pytest.raises(SyncError, match="local_data_exists"):
        join_server(root, site.server_engine, clock, site.server_clock)


@pytest.mark.mssql
def test_two_gates_with_sql_server(tmp_path, clock):
    from tests.support.mssql import temporary_database, unavailable_reason

    if unavailable_reason():
        pytest.skip(unavailable_reason())
    clock.set(at(MON, 10))
    with temporary_database() as engine:
        site = Site(tmp_path, engine, clock)
        try:
            g1, g2 = site.gate("gate1", 1), site.gate("gate2", 2)
            entry = GateService(g1).register_entry(PLATE)
            PeopleService(g1).create_shop("مبلمان آرتا")
            site.sync(g1)
            site.sync(g1)  # idempotent
            site.sync(g2)
            gate2 = GateService(g2)
            clock.advance(minutes=20)
            gate2.complete_exit(gate2.quote(gate2.resolve(entry.payload).id), PaymentMethod.CASH)
            site.sync(g2)
            site.sync(g1)
            assert count(g1, ActiveSession) == 0
            assert count(engine, Visit) == 1
            assert count(engine, Shop) == 1
            from sqlalchemy import text

            with engine.connect() as conn:
                drift = (
                    clock.now_utc()
                    - conn.execute(text("SELECT SYSUTCDATETIME()")).scalar_one().replace(tzinfo=clock.now_utc().tzinfo)
                ).total_seconds()
            assert abs(drift) > 0  # the server clock is read with T-SQL (fixed test clock differs)
        finally:
            site.close()
