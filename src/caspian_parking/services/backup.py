"""Backup & restore (SPEC §4.13).

Gate / standalone: one zip per backup = SQLite online-backup copy of the database + the
``receipt``, ``templates``, ``ads`` and ``config`` folders of the data root. Copied to every
destination, old ones removed by the retention policy. Server: T-SQL ``BACKUP DATABASE`` (.bak).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from caspian_parking.core.jalali import to_local
from caspian_parking.core.permissions import Permission
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting

log = logging.getLogger(__name__)

FOLDERS = ("receipt", "templates", "ads", "config")
DB_ENTRY = "database/local.db"
MANIFEST = "manifest.json"
STATE_FILE = "backup_state.json"
PREFIX = "parking-backup-"
OVERDUE = timedelta(hours=24)


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created: datetime
    size: int


@dataclass(frozen=True)
class RestoreCheck:
    ok: bool
    integrity: str
    tables: int
    revision: str | None
    visits: int


# ---------------------------------------------------------------- state (per machine)


def _state_path(ctx: AppContext) -> Path:
    return ctx.data_root.config / STATE_FILE


def last_success(ctx: AppContext) -> datetime | None:
    path = _state_path(ctx)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get("last_success")
    return datetime.fromisoformat(value) if value else None


def _remember(ctx: AppContext, when: datetime) -> None:
    path = _state_path(ctx)
    path.write_text(json.dumps({"last_success": when.isoformat()}), encoding="utf-8")


def overdue(ctx: AppContext) -> bool:
    """True when there was no successful backup in the last 24 hours (alert bar warning)."""
    last = last_success(ctx)
    return last is None or ctx.clock.now_utc() - last > OVERDUE


# ---------------------------------------------------------------- create


def destinations(ctx: AppContext) -> list[Path]:
    """The data root's backups folder plus this PC's extra folders (second disk, USB …)."""
    return [ctx.data_root.backups, *(Path(p) for p in ctx.config.backup_destinations if p)]


def sqlite_copy(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database (online backup API; WAL-safe)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(ctx: AppContext, reason: str = "manual") -> list[BackupInfo]:
    now = ctx.clock.now_utc()
    stamp = to_local(now).strftime("%Y%m%d-%H%M%S")
    name = f"{PREFIX}{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "local.db"
        sqlite_copy(ctx.data_root.training_db if ctx.training else ctx.data_root.local_db, db_copy)
        archive = Path(tmp) / name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(db_copy, DB_ENTRY)
            for folder in FOLDERS:
                base = ctx.data_root.root / folder
                for file in base.rglob("*") if base.is_dir() else []:
                    if file.is_file() and file.name != STATE_FILE:
                        bundle.write(file, f"files/{folder}/{file.relative_to(base).as_posix()}")
            bundle.writestr(
                MANIFEST,
                json.dumps(
                    {"created_utc": now.isoformat(), "node": ctx.node_id, "reason": reason, "training": ctx.training}
                ),
            )
        results: list[BackupInfo] = []
        for destination in destinations(ctx):
            try:
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / name
                shutil.copy2(archive, target)
                results.append(BackupInfo(target, now, target.stat().st_size))
                apply_retention(ctx, destination)
            except OSError as exc:  # e.g. USB disk not connected: other destinations still work
                log.warning("backup destination %s failed: %s", destination, exc)
    if not results:
        raise BackupError("backup.all_failed")
    _remember(ctx, now)
    log.info("backup %s written to %d destination(s)", name, len(results))
    return results


def list_backups(folder: Path) -> list[BackupInfo]:
    if not folder.is_dir():
        return []
    items = []
    for path in folder.glob(f"{PREFIX}*.zip"):
        stat = path.stat()
        items.append(BackupInfo(path, datetime.fromtimestamp(stat.st_mtime).astimezone(), stat.st_size))
    return sorted(items, key=lambda b: b.path.name, reverse=True)


def apply_retention(ctx: AppContext, folder: Path) -> list[Path]:
    with ctx.read() as session:
        keep = max(1, int(get_setting(session, "backup.keep")))
    removed = []
    for info in list_backups(folder)[keep:]:
        info.path.unlink(missing_ok=True)
        removed.append(info.path)
    return removed


# ---------------------------------------------------------------- restore


def test_restore(archive: Path) -> RestoreCheck:
    """Restore into a temporary folder and check integrity (the live database is not touched)."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extract(DB_ENTRY, tmp)
        except (KeyError, zipfile.BadZipFile) as exc:
            raise BackupError("backup.bad_archive") from exc
        connection = sqlite3.connect(Path(tmp) / DB_ENTRY)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            tables = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            visits = connection.execute("SELECT count(*) FROM visits").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise BackupError("backup.bad_archive") from exc
        finally:
            connection.close()
    return RestoreCheck(integrity == "ok", integrity, int(tables), revision[0] if revision else None, int(visits))


def restore_backup(ctx: AppContext, archive: Path) -> Path:
    """Replace the local database and folders with the backup. The app must restart afterwards.

    A safety backup of the current state is taken first and its path returned.
    """
    if not ctx.can(Permission.BACKUP_RESTORE):
        raise BackupError("gate.permission_denied")
    check = test_restore(archive)
    if not check.ok:
        raise BackupError("backup.integrity_failed")
    safety = create_backup(ctx, reason="before-restore")[0].path
    ctx.engine.dispose()
    target = ctx.data_root.training_db if ctx.training else ctx.data_root.local_db
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as bundle:
        bundle.extractall(tmp)
        for suffix in ("-wal", "-shm"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        os.replace(Path(tmp) / DB_ENTRY, target)
        files = Path(tmp) / "files"
        for folder in FOLDERS:
            source = files / folder
            if not source.is_dir():
                continue
            for file in source.rglob("*"):
                if file.is_file():
                    destination = ctx.data_root.root / folder / file.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, destination)
    log.warning("database restored from %s (safety backup %s)", archive, safety)
    return safety


# ---------------------------------------------------------------- SQL Server (server role)


def _run_to_completion(engine: Engine, sql: str, *params: object) -> None:
    """Run a T-SQL BACKUP/RESTORE and drain every informational result set (pyodbc stops early otherwise)."""
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True  # type: ignore[union-attr]
        cursor = raw.cursor()
        cursor.execute(sql, params)
        while cursor.nextset():
            pass
        cursor.close()
    finally:
        raw.close()


def mssql_backup(engine: Engine, database: str, path: Path) -> Path:
    """``BACKUP DATABASE`` to a .bak file (the path is on the SQL Server machine)."""
    _run_to_completion(engine, f"BACKUP DATABASE [{database}] TO DISK = ? WITH INIT, CHECKSUM", str(path))
    return path


def mssql_verify(engine: Engine, path: Path) -> bool:
    try:
        _run_to_completion(engine, "RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM", str(path))
    except Exception as exc:
        log.warning("verify failed: %s", exc)
        return False
    return True
