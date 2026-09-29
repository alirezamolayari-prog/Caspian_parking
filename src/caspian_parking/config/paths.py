"""Data root (SPEC §2.6): configurable, never inside Program Files, never removed by uninstall."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from caspian_parking.config.defaults import app_defaults

DATA_ROOT_ENV = "PARKING_DATA_ROOT"

SUBFOLDERS: tuple[str, ...] = (
    "receipt",
    "receipt/barcode-art",
    "templates",
    "ads",
    "ads/slideshow",
    "photos",
    "backups",
    "reports",
    "exports",
    "logs",
    "config",
    "db",
)


def _pointer_file() -> Path:
    program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    return program_data / str(app_defaults()["product_folder"]) / "dataroot.txt"


def resolve_data_root() -> Path:
    """Environment variable → pointer file written by the installer/wizard → default."""
    env = os.environ.get(DATA_ROOT_ENV)
    if env:
        return Path(env)
    pointer = _pointer_file()
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text:
            return Path(text)
    return Path(str(app_defaults()["data_root_default"]))


def write_data_root_pointer(root: Path) -> None:
    pointer = _pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(root), encoding="utf-8")


@dataclass(frozen=True)
class DataRoot:
    root: Path

    def ensure(self) -> DataRoot:
        for sub in SUBFOLDERS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self

    def sub(self, name: str) -> Path:
        return self.root / name

    @property
    def receipt(self) -> Path:
        return self.root / "receipt"

    @property
    def barcode_art(self) -> Path:
        return self.root / "receipt" / "barcode-art"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def ads(self) -> Path:
        return self.root / "ads"

    @property
    def slideshow(self) -> Path:
        return self.root / "ads" / "slideshow"

    @property
    def photos(self) -> Path:
        return self.root / "photos"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def db(self) -> Path:
        return self.root / "db"

    @property
    def local_db(self) -> Path:
        return self.db / str(app_defaults()["local_db_file"])

    @property
    def training_db(self) -> Path:
        return self.db / str(app_defaults()["training_db_file"])
