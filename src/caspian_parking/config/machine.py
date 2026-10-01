"""Machine-level configuration (``config\\settings.json``).

Only facts about *this* PC live here (role, node id, gate, server address). Everything that
must be the same on every PC (tariffs, texts, users…) is reference data in the database.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from caspian_parking.core.ids import uuid7

CONFIG_FILE = "settings.json"


class Role(StrEnum):
    STANDALONE = "standalone"
    GATE = "gate"
    SERVER = "server"


@dataclass
class ServerConfig:
    host: str = ""
    database: str = "parking"
    driver: str = "ODBC Driver 18 for SQL Server"
    windows_auth: bool = True
    user: str = ""
    encrypt: bool = False
    trust_server_certificate: bool = True


@dataclass
class DeviceConfig:
    """Hardware attached to this PC (SPEC §6). Everything defaults to simulators."""

    printer_backend: str = "simulator"  # simulator | windows | escpos
    printer_name: str = ""
    scanner_mode: str = "wedge"  # wedge (USB keyboard) | serial | off
    scanner_port: str = ""
    scanner_baud: int = 9600


@dataclass
class CameraConfig:
    """One lane camera of this gate (SPEC §6 PlateSource)."""

    lane: str = "entry"  # entry | exit
    name: str = ""
    kind: str = "off"  # off | simulator | rtsp | smart
    url: str = ""  # RTSP / ONVIF stream URL (password kept in the URL is never logged)
    port: int = 0  # smart camera HTTP push listener port
    engine: str = "none"  # none | simulator | plugin:<file.py>:<Class>
    min_confidence: int = 80  # percent

    @property
    def enabled(self) -> bool:
        return self.kind != "off"


@dataclass
class MachineConfig:
    node_id: str = field(default_factory=uuid7)
    node_name: str = field(default_factory=socket.gethostname)
    role: Role = Role.STANDALONE
    gate_code: int | None = 1
    server: ServerConfig = field(default_factory=ServerConfig)
    devices: DeviceConfig = field(default_factory=DeviceConfig)
    theme_default: str = "dark"
    language: str = "fa"
    training_mode: bool = False
    first_run_done: bool = False
    backup_destinations: list[str] = field(default_factory=list)
    photos_folder: str = ""  # empty = <data root>\photos
    cameras: list[CameraConfig] = field(default_factory=list)

    def camera_for(self, lane: str) -> CameraConfig | None:
        return next((c for c in self.cameras if c.lane == lane and c.enabled), None)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["role"] = self.role.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MachineConfig:
        server_fields = ServerConfig.__dataclass_fields__
        server = ServerConfig(**{k: v for k, v in data.get("server", {}).items() if k in server_fields})
        device_fields = DeviceConfig.__dataclass_fields__
        devices = DeviceConfig(**{k: v for k, v in data.get("devices", {}).items() if k in device_fields})
        camera_fields = CameraConfig.__dataclass_fields__
        cameras = [
            CameraConfig(**{k: v for k, v in item.items() if k in camera_fields}) for item in data.get("cameras", [])
        ]
        nested = ("server", "devices", "cameras")
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k not in nested}
        if "role" in known:
            known["role"] = Role(known["role"])
        return cls(server=server, devices=devices, cameras=cameras, **known)


def load_machine_config(config_dir: Path) -> MachineConfig:
    """Load settings.json, creating it (with a new node id) on first start."""
    path = config_dir / CONFIG_FILE
    if path.is_file():
        return MachineConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
    config = MachineConfig()
    save_machine_config(config_dir, config)
    return config


def save_machine_config(config_dir: Path, config: MachineConfig) -> None:
    """Atomic write so a power cut never leaves a half-written file."""
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / CONFIG_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
