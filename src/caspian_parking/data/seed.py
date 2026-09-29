"""Idempotent seeding of reference data for a fresh installation (values from resources/seed)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from caspian_parking.config.defaults import site_seed
from caspian_parking.config.machine import MachineConfig
from caspian_parking.core.permissions import BUILTIN_PRESETS
from caspian_parking.data.models import Gate, Level, Node, RolePreset
from caspian_parking.data.repositories.system import (
    GateRepository,
    LevelRepository,
    NodeRepository,
    RolePresetRepository,
    SettingsRepository,
)
from caspian_parking.i18n import tr


def seed_defaults(session: Session, seed: dict[str, Any] | None = None) -> None:
    data = seed if seed is not None else site_seed()

    presets = RolePresetRepository(session)
    for code, permissions in BUILTIN_PRESETS.items():
        if presets.by_code(code) is None:
            presets.add(
                RolePreset(code=code, name=tr(f"role.{code}"), permissions=sorted(permissions), is_builtin=True)
            )

    gates = GateRepository(session)
    for index, gate in enumerate(data.get("gates", [])):
        if gates.by_code(int(gate["code"])) is None:
            gates.add(Gate(code=int(gate["code"]), name=gate["name"], sort=index))

    levels = LevelRepository(session)
    for level in data.get("levels", []):
        if levels.by_code(level["code"]) is None:
            levels.add(
                Level(
                    code=level["code"],
                    name=level["name"],
                    capacity=int(level["capacity"]),
                    is_parking=bool(level["is_parking"]),
                    is_open=bool(level["is_open"]),
                    sort=int(level.get("sort", 0)),
                )
            )

    settings = SettingsRepository(session)
    for key, value in data.get("settings", {}).items():
        if settings.by_key(key) is None:
            settings.set(key, value)


def register_node(session: Session, config: MachineConfig) -> Node:
    """Make sure this PC exists in ``nodes`` (its id is the machine node id)."""
    repo = NodeRepository(session)
    node = repo.get(config.node_id)
    if node is None:
        return repo.add(
            Node(id=config.node_id, name=config.node_name, role=config.role.value, gate_code=config.gate_code)
        )
    if (node.name, node.role, node.gate_code) != (config.node_name, config.role.value, config.gate_code):
        repo.update(node, name=config.node_name, role=config.role.value, gate_code=config.gate_code)
    return node
