"""Gate ↔ server sync (SPEC §2.2, DECISIONS D-011, D-083…D-090).

Gates talk to the central SQL Server directly (ODBC). Every write on a gate is queued in ``sync_outbox``
in the same transaction; every write on the server is appended to ``sync_log`` (a sequence).

* **push** – queued rows are copied to the server. Events (append-only) are inserted if absent, so a
  re-push never duplicates. Reference rows follow the merge rule: higher ``(row_version, updated_at)``
  wins; the losing version is kept in ``audit_log`` and put in the review queue.
* **pull** – the gate reads ``sync_log`` after its cursor and applies other writers' rows the same way,
  keeping its local projection of vehicles inside (``active_sessions``) up to date.
* duplicate heuristics (same subscriber / shop, same amount, same day, different PCs) create review
  items on the server; nothing is ever deleted automatically.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Connection, Engine, Table, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from caspian_parking.core.clock import Clock
from caspian_parking.core.ids import uuid7
from caspian_parking.core.jalali import local_date, local_day_range_utc
from caspian_parking.data.base import Base
from caspian_parking.data.models import (
    ActiveSession,
    AuditLog,
    Cancellation,
    EntryEvent,
    ExitEvent,
    ReviewItem,
    SessionAlias,
    SharedSecret,
    SubscriptionPayment,
    SyncLog,
    SyncOutbox,
    SyncState,
    WalletTransaction,
)
from caspian_parking.data.session import LOCAL_ONLY_TABLES, append_only_tables
from caspian_parking.services.context import AppContext

log = logging.getLogger(__name__)

HUB_WRITER = "sync-hub"  # writer of rows created by the sync itself on the server (conflicts, review items)
TICKET_KEY = "ticket_hmac"
CURSOR_KEY = "pull_cursor"
LAST_SYNC_KEY = "last_sync_utc"
GAP_GRACE = timedelta(minutes=2)  # a missing sequence number older than this was a rolled-back transaction
DRIFT_LIMIT_S = 60.0
PUSH_BATCH = 500
PULL_BATCH = 1000
_TECHNICAL = frozenset({"sync_outbox", "sync_state", "sync_log", "shared_secrets", "gate_sequences", "alembic_version"})
_VERSION_FIELDS = ("row_version", "updated_at_utc", "updated_by")


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncStatus:
    online: bool
    last_sync: datetime | None = None
    pushed: int = 0
    pulled: int = 0
    pending: int = 0
    drift_seconds: float | None = None
    error: str | None = None

    @property
    def drift_warning(self) -> bool:
        return self.drift_seconds is not None and abs(self.drift_seconds) > DRIFT_LIMIT_S


def _t(model: Any) -> Table:
    return cast(Table, model.__table__)


def synced_tables() -> dict[str, Table]:
    return {
        name: table
        for name, table in Base.metadata.tables.items()
        if name not in _TECHNICAL and name not in LOCAL_ONLY_TABLES and "origin_node" in table.c
    }


def _is_reference(table: Table) -> bool:
    return "row_version" in table.c


def _is_event(table: Table) -> bool:
    return table.name in append_only_tables()


def _merge_key(row: dict[str, Any]) -> tuple[int, datetime]:
    updated = row.get("updated_at_utc") or datetime.min.replace(tzinfo=UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return int(row.get("row_version") or 0), updated


def _content(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _VERSION_FIELDS}


def _normal(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ca, cb = _content(a), _content(b)
    return ca.keys() == cb.keys() and all(_normal(ca[k]) == _normal(cb[k]) for k in ca)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _load(conn: Connection, table: Table, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    id_list = sorted(set(ids))
    for start in range(0, len(id_list), 500):
        chunk = id_list[start : start + 500]
        for row in conn.execute(select(table).where(table.c.id.in_(chunk))):
            rows[row.id] = dict(row._mapping)
    return rows


def server_now(conn: Connection) -> datetime:
    """The server clock is the time reference (SPEC §2.2)."""
    value: datetime
    if conn.dialect.name == "mssql":
        value = conn.execute(text("SELECT SYSUTCDATETIME()")).scalar_one()
    else:
        value = datetime.fromisoformat(
            str(conn.execute(text("SELECT strftime('%Y-%m-%d %H:%M:%f', 'now')")).scalar_one())
        )
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# ---------------------------------------------------------------- applying rows


@dataclass
class _Applied:
    inserted: bool = False
    updated: bool = False
    conflict: dict[str, Any] | None = None  # the losing version of a concurrently edited reference row
    winner: dict[str, Any] | None = None

    @property
    def changed(self) -> bool:
        return self.inserted or self.updated


def _apply_row(conn: Connection, table: Table, incoming: dict[str, Any], existing: dict[str, Any] | None) -> _Applied:
    if existing is None:
        conn.execute(insert(table), [incoming])
        return _Applied(inserted=True)
    if _is_event(table):
        return _Applied()  # events never change: already there = nothing to do (idempotent re-push)
    if _same(incoming, existing):
        return _Applied()
    if not _is_reference(table):  # mutable history rows (visits): the latest write wins
        conn.execute(update(table).where(table.c.id == incoming["id"]).values(**incoming))
        return _Applied(updated=True)
    if _merge_key(incoming) > _merge_key(existing):
        conn.execute(update(table).where(table.c.id == incoming["id"]).values(**incoming))
        # same version edited on two PCs: the earlier edit loses but is kept for review
        concurrent = int(incoming.get("row_version") or 0) == int(existing.get("row_version") or 0)
        return _Applied(updated=True, conflict=existing if concurrent else None, winner=incoming)
    return _Applied(conflict=incoming, winner=existing)


def _hub_insert(conn: Connection, model: Any, values: dict[str, Any], now: datetime) -> None:
    conn.execute(insert(_t(model)), [values])
    conn.execute(
        insert(_t(SyncLog)),
        [{"table_name": model.__tablename__, "row_id": values["id"], "writer_node": HUB_WRITER, "logged_at_utc": now}],
    )


def _review(
    conn: Connection, kind: str, dedupe_key: str, summary: str, refs: dict[str, Any], node: str, now: datetime
) -> bool:
    if conn.execute(select(ReviewItem.id).where(ReviewItem.dedupe_key == dedupe_key)).first() is not None:
        return False
    _hub_insert(
        conn,
        ReviewItem,
        {
            "id": uuid7(),
            "origin_node": node,
            "created_at_utc": now,
            "created_by": None,
            "row_version": 1,
            "updated_at_utc": now,
            "updated_by": None,
            "is_active": True,
            "kind": kind,
            "dedupe_key": dedupe_key[:250],
            "status": "open",
            "summary": summary[:400],
            "refs": refs,
            "resolution": None,
        },
        now,
    )
    return True


def _record_conflict(
    conn: Connection, table: Table, loser: dict[str, Any], winner: dict[str, Any], now: datetime
) -> None:
    version, updated = _merge_key(loser)
    key = f"conflict:{table.name}:{loser['id']}:{version}:{updated.isoformat()}"
    if conn.execute(select(ReviewItem.id).where(ReviewItem.dedupe_key == key)).first() is not None:
        return
    _hub_insert(
        conn,
        AuditLog,
        {
            "id": uuid7(),
            "origin_node": loser.get("origin_node") or HUB_WRITER,
            "created_at_utc": now,
            "created_by": loser.get("updated_by"),
            "entity": table.name,
            "entity_id": loser["id"],
            "action": "sync_conflict",
            "changes": {"lost": _jsonable(_content(loser)), "kept_version": int(winner.get("row_version") or 0)},
            "reason": None,
        },
        now,
    )
    _review(
        conn,
        "conflict",
        key,
        f"{table.name}:{loser['id']}",
        {"table": table.name, "row_id": loser["id"], "lost_version": version},
        HUB_WRITER,
        now,
    )


# ---------------------------------------------------------------- duplicate heuristics (server side)


def _cancelled_ids(conn: Connection, table_name: str) -> set[str]:
    return set(conn.execute(select(Cancellation.target_id).where(Cancellation.target_table == table_name)).scalars())


def detect_duplicates(conn: Connection, table_name: str, row_ids: Iterable[str], now: datetime) -> int:
    """Same subscriber (or shop wallet), same amount, same Tehran day, entered on different PCs → review."""
    created = 0
    model: Any
    if table_name == "subscription_payments":
        model, owner, when = SubscriptionPayment, SubscriptionPayment.person_id, SubscriptionPayment.paid_at_utc
        kind, amount_col = "duplicate_payment", SubscriptionPayment.amount
    elif table_name == "wallet_transactions":
        model, owner, when = WalletTransaction, WalletTransaction.shop_id, WalletTransaction.created_at_utc
        kind, amount_col = "duplicate_deposit", WalletTransaction.amount
    else:
        return 0
    cancelled = _cancelled_ids(conn, table_name)
    rows = conn.execute(select(_t(model)).where(_t(model).c.id.in_(list(row_ids)))).mappings().all()
    for row in rows:
        if table_name == "wallet_transactions" and row["kind"] != "deposit":
            continue
        moment = _normal(row[when.key])
        start, end = local_day_range_utc(local_date(moment))
        stmt = select(_t(model).c.id, _t(model).c.origin_node).where(
            owner == row[owner.key],
            amount_col == row[amount_col.key],
            when >= start,
            when < end,
        )
        if table_name == "wallet_transactions":
            stmt = stmt.where(WalletTransaction.kind == "deposit")
        same = [r for r in conn.execute(stmt) if r.id not in cancelled]
        nodes = {r.origin_node for r in same}
        if len(same) < 2 or len(nodes) < 2:
            continue
        ids = sorted(r.id for r in same)
        if _review(
            conn,
            kind,
            f"{kind}:" + "|".join(ids),
            f"{table_name}:{row[owner.key]}:{row[amount_col.key]}",
            {"table": table_name, "ids": ids, "owner": row[owner.key], "amount": int(row[amount_col.key])},
            HUB_WRITER,
            now,
        ):
            created += 1
    return created


# ---------------------------------------------------------------- local projection of vehicles inside


def _session_closed(conn: Connection, session_id: str) -> bool:
    if conn.execute(select(ExitEvent.id).where(ExitEvent.session_id == session_id)).first():
        return True
    if conn.execute(
        select(Cancellation.id).where(
            Cancellation.session_id == session_id, Cancellation.target_table == "entry_events"
        )
    ).first():
        return True
    return conn.execute(select(SessionAlias.id).where(SessionAlias.session_id == session_id)).first() is not None


def _project(conn: Connection, table_name: str, row: dict[str, Any], node_id: str, now: datetime) -> None:
    """Keep ``active_sessions`` (this PC's list of vehicles inside) in step with other gates' events."""
    active = _t(ActiveSession)
    if table_name == "entry_events":
        _project_entry(conn, row, node_id, now)
    elif (
        table_name == "exit_events"
        or (table_name == "cancellations" and row.get("target_table") == "entry_events" and row.get("session_id"))
        or table_name == "session_aliases"
    ):
        conn.execute(delete(active).where(active.c.id == row["session_id"]))
    elif table_name == "night_marks" and row.get("session_id"):
        conn.execute(update(active).where(active.c.id == row["session_id"]).values(night_marked=True))


def _project_entry(conn: Connection, row: dict[str, Any], node_id: str, now: datetime) -> None:
    from caspian_parking.data.models import Visit

    active = _t(ActiveSession)
    session_id = row["session_id"]
    if _session_closed(conn, session_id) or conn.execute(select(active.c.id).where(active.c.id == session_id)).first():
        return
    same_ticket = (active.c.gate_code == row["gate_code"], active.c.ticket_no == row["ticket_no"])
    # 1. this gate adopted the ticket offline and the car is still inside: the real session replaces it
    conn.execute(delete(active).where(*same_ticket, active.c.id != session_id))
    # 2. this gate already let the car out under a provisional session: link the two, nothing is inside
    provisional = conn.execute(
        select(Visit.session_id).where(
            Visit.gate_in == row["gate_code"],
            Visit.ticket_no == row["ticket_no"],
            Visit.session_id != session_id,
            Visit.session_id.not_in(select(EntryEvent.session_id)),
            Visit.session_id.not_in(select(SessionAlias.provisional_session_id)),
        )
    ).first()
    if provisional is not None:
        _alias(conn, provisional.session_id, session_id, node_id, now)
        return
    values = {c.name: row[c.name] for c in active.columns if c.name in row and c.name != "id"}
    values.update(id=session_id, night_marked=False, duplicate_count=0, flags=[])
    conn.execute(insert(active), [values])


def _alias(conn: Connection, provisional_id: str, session_id: str, node_id: str, now: datetime) -> None:
    alias_id = uuid7()
    conn.execute(
        insert(_t(SessionAlias)),
        [
            {
                "id": alias_id,
                "origin_node": node_id,
                "created_at_utc": now,
                "created_by": None,
                "provisional_session_id": provisional_id,
                "session_id": session_id,
            }
        ],
    )
    conn.execute(
        insert(_t(SyncOutbox)),
        [{"id": uuid7(), "table_name": "session_aliases", "row_id": alias_id, "queued_at_utc": now}],
    )


# ---------------------------------------------------------------- the engine


def _state(conn: Connection, key: str) -> str | None:
    return conn.execute(select(SyncState.value).where(SyncState.key == key)).scalar_one_or_none()


def _set_state(conn: Connection, key: str, value: str) -> None:
    if _state(conn, key) is None:
        conn.execute(insert(_t(SyncState)), [{"key": key, "value": value}])
    else:
        conn.execute(update(_t(SyncState)).where(SyncState.key == key).values(value=value))


class SyncEngine:
    def __init__(
        self, ctx: AppContext, server: Engine, server_clock: Callable[[Connection], datetime] = server_now
    ) -> None:
        self.ctx = ctx
        self.server = server
        self.server_clock = server_clock
        self.tables = synced_tables()

    @property
    def node_id(self) -> str:
        return self.ctx.node_id

    def pending(self) -> int:
        with self.ctx.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(SyncOutbox)).scalar_one())

    # ------------------------------------------------------------ push
    def push(self, batch: int = PUSH_BATCH) -> int:
        """Send queued rows; returns how many rows the server did not have yet (or accepted as newer)."""
        total = 0
        while True:
            with self.ctx.engine.connect() as local:
                queue = local.execute(select(SyncOutbox).order_by(SyncOutbox.id).limit(batch)).all()
                if not queue:
                    return total
                by_table: dict[str, set[str]] = defaultdict(set)
                for item in queue:
                    by_table[item.table_name].add(item.row_id)
                rows = {
                    name: _load(local, self.tables[name], ids) for name, ids in by_table.items() if name in self.tables
                }
            with self.server.begin() as remote:
                now = self.server_clock(remote)
                for name, local_rows in rows.items():
                    table = self.tables[name]
                    existing = _load(remote, table, local_rows)
                    changed_ids = []
                    for row_id, row in local_rows.items():
                        applied = _apply_row(remote, table, row, existing.get(row_id))
                        if applied.conflict is not None and applied.winner is not None:
                            _record_conflict(remote, table, applied.conflict, applied.winner, now)
                        if applied.changed:
                            changed_ids.append(row_id)
                            remote.execute(
                                insert(_t(SyncLog)),
                                [
                                    {
                                        "table_name": name,
                                        "row_id": row_id,
                                        "writer_node": self.node_id,
                                        "logged_at_utc": now,
                                    }
                                ],
                            )
                    total += len(changed_ids)
                    if changed_ids:
                        detect_duplicates(remote, name, changed_ids, now)
            with self.ctx.engine.begin() as local:
                local.execute(delete(_t(SyncOutbox)).where(SyncOutbox.id.in_([q.id for q in queue])))

    # ------------------------------------------------------------ pull
    def pull(self, batch: int = PULL_BATCH, include_own: bool = False) -> int:
        """Apply other writers' rows from the server log; returns the number of rows applied."""
        applied_total = 0
        while True:
            with self.ctx.engine.connect() as local:
                cursor = int(_state(local, CURSOR_KEY) or 0)
            with self.server.connect() as remote:
                now = self.server_clock(remote)
                entries = remote.execute(
                    select(SyncLog).where(SyncLog.seq > cursor).order_by(SyncLog.seq).limit(batch)
                ).all()
                accepted = []
                expected = cursor + 1
                for entry in entries:
                    if entry.seq != expected and _normal(entry.logged_at_utc) > now - GAP_GRACE:
                        break  # a lower sequence number may still be committing: wait for it
                    accepted.append(entry)
                    expected = entry.seq + 1
                wanted: dict[str, set[str]] = defaultdict(set)
                for entry in accepted:
                    if (include_own or entry.writer_node != self.node_id) and entry.table_name in self.tables:
                        wanted[entry.table_name].add(entry.row_id)
                server_rows = {name: _load(remote, self.tables[name], ids) for name, ids in wanted.items()}
            if not accepted:
                return applied_total
            with self.ctx.engine.begin() as local:
                local_now = self.ctx.clock.now_utc()
                done: set[tuple[str, str]] = set()
                for entry in accepted:
                    key = (entry.table_name, entry.row_id)
                    row = server_rows.get(entry.table_name, {}).get(entry.row_id)
                    if row is None or key in done:
                        continue
                    done.add(key)
                    table = self.tables[entry.table_name]
                    existing = _load(local, table, [entry.row_id]).get(entry.row_id)
                    result = _apply_row(local, table, row, existing)
                    if result.changed:
                        applied_total += 1
                        _project(local, entry.table_name, row, self.node_id, local_now)
                _set_state(local, CURSOR_KEY, str(accepted[-1].seq))
            if len(entries) < batch:
                return applied_total

    # ------------------------------------------------------------ clock & cycle
    def check_clock(self) -> float:
        with self.server.connect() as remote:
            reference = self.server_clock(remote)
        drift = (self.ctx.clock.now_utc() - reference).total_seconds()
        if abs(drift) > DRIFT_LIMIT_S:
            log.warning("clock drift of %.0f s against the server", drift)
        return drift

    def sync_once(self) -> SyncStatus:
        try:
            pushed = self.push()
            pulled = self.pull()
            drift = self.check_clock()
        except (DBAPIError, SQLAlchemyError, OSError) as exc:
            log.warning("sync failed: %s", exc)
            return SyncStatus(False, self.last_sync(), pending=self.pending(), error=str(exc).splitlines()[0][:200])
        now = self.ctx.clock.now_utc()
        with self.ctx.engine.begin() as local:
            _set_state(local, LAST_SYNC_KEY, now.isoformat())
        return SyncStatus(True, now, pushed, pulled, self.pending(), drift)

    def last_sync(self) -> datetime | None:
        with self.ctx.engine.connect() as local:
            value = _state(local, LAST_SYNC_KEY)
        return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- server preparation & joining


def ensure_server_log(server: Engine, writer: str) -> int:
    """Log every existing row once (a server database created before sync existed). Returns rows logged."""
    with server.begin() as conn:
        if conn.execute(select(func.count()).select_from(SyncLog)).scalar_one():
            return 0
        now = server_now(conn)
        logged = 0
        for name, table in synced_tables().items():
            for (row_id,) in conn.execute(select(table.c.id).order_by(table.c.created_at_utc, table.c.id)):
                conn.execute(
                    insert(_t(SyncLog)),
                    [{"table_name": name, "row_id": row_id, "writer_node": writer, "logged_at_utc": now}],
                )
                logged += 1
        return logged


def publish_ticket_key(server: Engine, key: bytes) -> bytes:
    """The server keeps the installation's ticket key; the first one published wins."""
    import base64

    with server.begin() as conn:
        current = conn.execute(select(SharedSecret.value).where(SharedSecret.name == TICKET_KEY)).scalar_one_or_none()
        if current is not None:
            return base64.b64decode(current)
        conn.execute(insert(_t(SharedSecret)), [{"name": TICKET_KEY, "value": base64.b64encode(key).decode()}])
        return key


def fetch_ticket_key(server: Engine) -> bytes | None:
    import base64

    with server.connect() as conn:
        value = conn.execute(select(SharedSecret.value).where(SharedSecret.name == TICKET_KEY)).scalar_one_or_none()
    return base64.b64decode(value) if value else None


BUSINESS_TABLES = ("entry_events", "exit_events", "payments", "subscription_payments", "wallet_transactions")


def has_business_data(engine: Engine) -> bool:
    with engine.connect() as conn:
        for name in BUSINESS_TABLES:
            table = Base.metadata.tables[name]
            if conn.execute(select(func.count()).select_from(table)).scalar_one():
                return True
    return False


def unsent_rows(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(SyncOutbox)).scalar_one())


def join_server(
    root: Path,
    server: Engine,
    clock: Clock | None = None,
    server_clock: Callable[[Connection], datetime] = server_now,
) -> datetime:
    """Turn this PC into a gate of ``server`` (SPEC §2.1, D-087).

    The local database must not contain business data yet (it is set aside, never deleted); the new
    local database receives the server's reference data and the other gates' events, the ticket key is
    stored DPAPI-protected, and this PC registers itself as a node.
    """
    from caspian_parking.config.machine import Role, load_machine_config, save_machine_config
    from caspian_parking.config.paths import DataRoot
    from caspian_parking.config.secrets import SecretStore
    from caspian_parking.core.clock import SYSTEM_CLOCK
    from caspian_parking.data.db import create_sqlite_engine
    from caspian_parking.data.migrate import upgrade
    from caspian_parking.data.seed import register_node
    from caspian_parking.data.session import make_session_factory
    from caspian_parking.services.gate_service import HMAC_SECRET

    clock = clock or SYSTEM_CLOCK
    data_root = DataRoot(root).ensure()
    config = load_machine_config(data_root.config)
    key = fetch_ticket_key(server)
    if key is None:
        raise SyncError("sync.server_not_ready")
    db_path = data_root.local_db
    if db_path.exists():
        engine = create_sqlite_engine(db_path)
        try:
            upgrade(engine)
            busy = has_business_data(engine) or unsent_rows(engine) > 0
        finally:
            engine.dispose()
        if busy:
            raise SyncError("sync.local_data_exists")
        stamp = clock.now_utc().strftime("%Y%m%d%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            old = db_path.with_name(db_path.name + suffix)
            if old.exists():
                old.rename(old.with_name(f"{db_path.stem}.prejoin-{stamp}{db_path.suffix}{suffix}"))
    SecretStore(data_root.config).set(HMAC_SECRET, key)
    config.role = Role.GATE
    now = clock.now_utc()
    config.joined_at = now.isoformat()
    save_machine_config(data_root.config, config)
    engine = create_sqlite_engine(db_path)
    try:
        upgrade(engine)
        ctx = AppContext(data_root, config, engine, make_session_factory(engine), clock=clock)
        SyncEngine(ctx, server, server_clock).pull(include_own=True)
        with ctx.uow() as session:
            register_node(session, config)
    finally:
        engine.dispose()
    return now
