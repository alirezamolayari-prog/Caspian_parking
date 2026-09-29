"""Photo storage (SPEC §4.13): Jalali folders, searchable file names, retention, usage, disk space.

File name example (searchable in Windows Explorer):
``1405-07-06_14-32-10_ورود_یافت‌آباد_12ب345-22_مشترک_علی‌رضایی_پژو206.jpg``
Photos of fled, blocked, night-parking and "keep forever" sessions are never deleted automatically.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select

from caspian_parking.core.jalali import jalali_of, to_local
from caspian_parking.data.models import NightMark, Photo, Visit
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.i18n import tr
from caspian_parking.services.context import AppContext
from caspian_parking.services.settings import get_setting

ZWNJ = chr(0x200C)
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
PROTECTED_STATUSES = ("fled", "recovered")
PROTECTED_KINDS = ("block",)
USAGE_WINDOW_DAYS = 30
BYTES_PER_MB = 1024 * 1024


class PhotoRepository(ReferenceRepository[Photo]):
    model = Photo


def sanitize(part: str) -> str:
    """Safe file-name part: invalid characters → '-', spaces → ZWNJ (keeps Persian names readable)."""
    cleaned = _INVALID.sub("-", part.strip())
    return _SPACES.sub(ZWNJ, cleaned).strip(".")


def photo_filename(
    moment: datetime,
    direction: str,
    gate_name: str,
    plate_text: str | None,
    category: str,
    person_name: str | None = None,
    vehicle_model: str | None = None,
    extension: str = "jpg",
) -> str:
    local = to_local(moment)
    j = jalali_of(moment)
    parts = [
        f"{j.year:04d}-{j.month:02d}-{j.day:02d}_{local.strftime('%H-%M-%S')}",
        tr(f"photo.{direction}"),
        gate_name,
        plate_text or tr("plate.empty"),
        tr(f"category.{category}"),
        person_name or tr("photo.transient"),
    ]
    if vehicle_model:
        parts.append(vehicle_model)
    return "_".join(sanitize(p) for p in parts if p) + f".{extension}"


def photos_root(ctx: AppContext) -> Path:
    return Path(ctx.config.photos_folder) if ctx.config.photos_folder else ctx.data_root.photos


def photo_dir(ctx: AppContext, moment: datetime) -> Path:
    j = jalali_of(moment)
    return photos_root(ctx) / f"{j.year:04d}" / f"{j.month:02d}" / f"{j.day:02d}"


def store_photo(
    ctx: AppContext,
    data: bytes,
    moment: datetime,
    kind: str,
    filename: str,
    session_id: str | None = None,
    keep_forever: bool = False,
) -> Photo:
    folder = photo_dir(ctx, moment)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    counter = 1
    while target.exists():
        target = folder / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    target.write_bytes(data)
    with ctx.uow() as session:
        return PhotoRepository(session).add(
            Photo(session_id=session_id, kind=kind, path=str(target), taken_at_utc=moment, keep_forever=keep_forever)
        )


def cleanup(ctx: AppContext) -> list[Path]:
    """Delete photo files older than the retention period, except protected ones."""
    now = ctx.clock.now_utc()
    with ctx.read() as session:
        days = int(get_setting(session, "photos.retention_days"))
    cutoff = now - timedelta(days=days)
    removed: list[Path] = []
    with ctx.uow() as session:
        protected_sessions = select(Visit.session_id).where(
            or_(Visit.status.in_(PROTECTED_STATUSES), Visit.night_count > 0)
        )
        night_sessions = select(NightMark.session_id)
        candidates = session.scalars(
            select(Photo).where(
                Photo.taken_at_utc < cutoff,
                Photo.file_deleted_at_utc.is_(None),
                Photo.keep_forever.is_(False),
                Photo.kind.not_in(PROTECTED_KINDS),
                or_(
                    Photo.session_id.is_(None),
                    Photo.session_id.not_in(protected_sessions),
                ),
                or_(Photo.session_id.is_(None), Photo.session_id.not_in(night_sessions)),
            )
        ).all()
        for photo in candidates:
            path = Path(photo.path)
            path.unlink(missing_ok=True)
            photo.file_deleted_at_utc = now
            removed.append(path)
    return removed


@dataclass(frozen=True)
class StorageUsage:
    mb_per_day: float
    free_bytes: int
    total_bytes: int
    days_left: int | None

    @property
    def free_percent(self) -> float:
        return 100.0 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0


def usage(ctx: AppContext) -> StorageUsage:
    root = photos_root(ctx)
    root.mkdir(parents=True, exist_ok=True)
    now = ctx.clock.now_utc()
    since = now - timedelta(days=USAGE_WINDOW_DAYS)
    total = 0
    with ctx.read() as session:
        for (path,) in session.execute(
            select(Photo.path).where(Photo.taken_at_utc >= since, Photo.file_deleted_at_utc.is_(None))
        ):
            file = Path(path)
            if file.is_file():
                total += file.stat().st_size
    mb_per_day = total / BYTES_PER_MB / USAGE_WINDOW_DAYS
    disk = shutil.disk_usage(root)
    days_left = int(disk.free / BYTES_PER_MB / mb_per_day) if mb_per_day > 0 else None
    return StorageUsage(round(mb_per_day, 2), disk.free, disk.total, days_left)


def disk_low(ctx: AppContext) -> bool:
    with ctx.read() as session:
        minimum = float(get_setting(session, "disk.min_free_percent"))
    return usage(ctx).free_percent < minimum
