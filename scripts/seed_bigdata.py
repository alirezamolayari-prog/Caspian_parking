"""Synthetic dataset for the performance targets of SPEC §2.4.

Creates a data root with 1,000,000 finished visits (≈ 3 years), their entry events and payments,
2,000 subscribers with plates, and ~200 vehicles currently inside.

Usage:  .venv\\Scripts\\python.exe scripts\\seed_bigdata.py [data_root] [--visits N]
Default data root: .bigdata\\data in the repository (git-ignored).
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from caspian_parking.core.ids import uuid7
from caspian_parking.core.plate import LETTERS
from caspian_parking.services.context import open_context

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / ".bigdata" / "data"
BATCH = 20_000
NODE = "seed-node"
FMT = "%Y-%m-%d %H:%M:%S.%f"
PRICES = (190_000, 200_000, 240_000, 380_000, 570_000, 760_000)


def ts(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).strftime(FMT)


def plate_key(rng: random.Random) -> str:
    letter = rng.choice(LETTERS[1:23])
    return f"{rng.randint(10, 99)}{letter}{rng.randint(100, 999)}-{rng.randint(10, 99)}"


def seed(
    root: Path, visits: int, subscribers: int = 2_000, inside: int = 200, seed_value: int = 1405
) -> dict[str, int]:
    rng = random.Random(seed_value)
    context = open_context(root)
    db_path = context.data_root.local_db
    context.close()
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA journal_mode=WAL")
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(days=3 * 365)
    stamp = ts(now)

    # ---- subscribers
    people_rows, plate_rows, subscriber_plates = [], [], []
    for index in range(subscribers):
        person_id = uuid7()
        end = now + timedelta(days=rng.randint(-40, 30))
        people_rows.append((person_id, NODE, stamp, 1, stamp, 1, "subscriber", f"مشترک{index}", "آزمایشی", ts(end)))
        key = plate_key(rng)
        subscriber_plates.append((person_id, key))
        plate_rows.append((uuid7(), NODE, stamp, 1, stamp, 1, person_id, key))
    connection.executemany(
        "INSERT INTO people (id, origin_node, created_at_utc, row_version, updated_at_utc, is_active, kind, first_name,"
        " last_name, subscription_end_utc, location_type, payer, max_concurrent, negative_allowed, negative_max_days,"
        " night_exempt) VALUES (?,?,?,?,?,?,?,?,?,?, 'inside', 'self', 1, 0, 10, 0)",
        people_rows,
    )
    connection.executemany(
        "INSERT INTO person_plates (id, origin_node, created_at_utc, row_version, updated_at_utc, is_active, person_id,"
        " plate_key) VALUES (?,?,?,?,?,?,?,?)",
        plate_rows,
    )
    connection.commit()

    # ---- visits, entry events, payments
    transient_plates = [plate_key(rng) for _ in range(150_000)]
    span = (now - start).total_seconds()
    visit_sql = (
        "INSERT INTO visits (id, origin_node, created_at_utc, session_id, plate_key, ticket_no, gate_in, gate_out,"
        " vehicle_type, kind, category, entry_at_utc, exit_at_utc, total_minutes, amount_due, amount_paid, status,"
        " lost_ticket, no_plate, night_count, person_id, flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,'[]')"
    )
    entry_sql = (
        "INSERT INTO entry_events (id, origin_node, created_at_utc, session_id, gate_code, ticket_sequence, ticket_no,"
        " plate_key, vehicle_type, kind, category, entry_at_utc, entry_minute, no_plate, person_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)"
    )
    payment_sql = (
        "INSERT INTO payments (id, origin_node, created_at_utc, session_id, purpose, method, amount, gate_code)"
        " VALUES (?,?,?,?, 'parking', ?,?,?)"
    )
    sequence = {1: 0, 2: 0}
    visit_batch: list[tuple] = []
    entry_batch: list[tuple] = []
    payment_batch: list[tuple] = []
    methods = ("cash", "card", "mall_card")
    for index in range(visits):
        entry_at = start + timedelta(seconds=span * index / visits + rng.randint(0, 60))
        minutes = rng.choice((5, 20, 45, 61, 73, 90, 120, 150, 200, 300))
        exit_at = entry_at + timedelta(minutes=minutes)
        gate = 1 if rng.random() < 0.6 else 2
        sequence[gate] += 1
        session_id = uuid7()
        subscriber = rng.random() < 0.1
        if subscriber:
            person_id, key = subscriber_plates[rng.randrange(subscribers)]
            category, amount = "subscriber", 0
        else:
            person_id, key = None, rng.choice(transient_plates)
            category, amount = "transient", rng.choice(PRICES)
        vehicle = "motorcycle" if rng.random() < 0.05 else "sedan"
        ticket = f"{gate}-{sequence[gate]:05d}-0"
        night = 1 if rng.random() < 0.01 else 0
        visit_batch.append(
            (uuid7(), NODE, ts(exit_at), session_id, key, ticket, gate, gate, vehicle, "transient", category,
             ts(entry_at), ts(exit_at), minutes, amount, amount, "paid" if amount else "free", night, person_id)
        )  # fmt: skip
        entry_batch.append(
            (uuid7(), NODE, ts(entry_at), session_id, gate, sequence[gate], ticket, key, vehicle, "transient",
             category, ts(entry_at), int(entry_at.timestamp() // 60), person_id)
        )  # fmt: skip
        if amount:
            payment_batch.append((uuid7(), NODE, ts(exit_at), session_id, rng.choice(methods), amount, gate))
        if len(visit_batch) >= BATCH:
            connection.executemany(visit_sql, visit_batch)
            connection.executemany(entry_sql, entry_batch)
            connection.executemany(payment_sql, payment_batch)
            connection.commit()
            visit_batch, entry_batch, payment_batch = [], [], []
    if visit_batch:
        connection.executemany(visit_sql, visit_batch)
        connection.executemany(entry_sql, entry_batch)
        connection.executemany(payment_sql, payment_batch)

    # ---- vehicles inside now
    for index in range(inside):
        gate = 1
        sequence[gate] += 1
        entry_at = now - timedelta(minutes=rng.randint(5, 600))
        session_id = uuid7()
        key = f"{10 + index % 90}ب{100 + index}-{11 + index % 80}"
        values = (session_id, NODE, ts(entry_at), gate, sequence[gate], f"1-{sequence[gate]:05d}-0", key, "sedan",
                  "transient", "transient", ts(entry_at), int(entry_at.timestamp() // 60))  # fmt: skip
        connection.execute(
            "INSERT INTO active_sessions (id, origin_node, created_at_utc, gate_code, ticket_sequence, ticket_no,"
            " plate_key, vehicle_type, kind, category, entry_at_utc, entry_minute, no_plate, night_marked,"
            " duplicate_count, flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,'[]')",
            values,
        )
    connection.execute("INSERT OR REPLACE INTO gate_sequences (gate_code, last_sequence) VALUES (1, ?)", (sequence[1],))
    connection.execute("INSERT OR REPLACE INTO gate_sequences (gate_code, last_sequence) VALUES (2, ?)", (sequence[2],))
    connection.commit()
    connection.execute("ANALYZE")
    connection.close()
    return {"visits": visits, "subscribers": subscribers, "inside": inside}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--visits", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    if (args.root / "db" / "local.db").exists():
        print(f"dataset already exists in {args.root} — delete the folder to rebuild")
        return 0
    started = time.perf_counter()
    counts = seed(args.root, args.visits)
    print(f"seeded {counts} into {args.root} in {time.perf_counter() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
