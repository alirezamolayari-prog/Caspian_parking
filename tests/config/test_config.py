from __future__ import annotations

import json
import logging

import pytest

from caspian_parking.config.logging_setup import setup_logging
from caspian_parking.config.machine import MachineConfig, Role, load_machine_config, save_machine_config
from caspian_parking.config.paths import DATA_ROOT_ENV, SUBFOLDERS, DataRoot, resolve_data_root
from caspian_parking.config.secrets import SecretStore
from caspian_parking.core.ids import is_uuid7


def test_data_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "root"))
    assert resolve_data_root() == tmp_path / "root"


def test_data_root_creates_all_folders(tmp_path):
    root = DataRoot(tmp_path).ensure()
    for sub in SUBFOLDERS:
        assert (tmp_path / sub).is_dir()
    assert root.local_db.parent == root.db
    assert root.barcode_art == tmp_path / "receipt" / "barcode-art"


def test_machine_config_created_once_and_persisted(tmp_path):
    first = load_machine_config(tmp_path)
    assert is_uuid7(first.node_id)
    assert first.role is Role.STANDALONE
    first.role = Role.GATE
    first.gate_code = 2
    first.server.host = "SERVER01"
    save_machine_config(tmp_path, first)
    again = load_machine_config(tmp_path)
    assert again.node_id == first.node_id
    assert again.role is Role.GATE
    assert again.server.host == "SERVER01"
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["gate_code"] == 2


def test_machine_config_ignores_unknown_keys():
    config = MachineConfig.from_json({"role": "server", "future_field": 1, "server": {"host": "x", "zzz": 1}})
    assert config.role is Role.SERVER
    assert config.server.host == "x"


def test_secret_store_encrypts_with_dpapi(tmp_path):
    store = SecretStore(tmp_path)
    store.set("sql_password", "p@ss-ورود")
    raw = (tmp_path / "secrets.json").read_text(encoding="utf-8")
    assert "p@ss" not in raw
    assert store.get_text("sql_password") == "p@ss-ورود"
    key = store.get_or_create("hmac_key")
    assert len(key) == 32
    assert store.get_or_create("hmac_key") == key
    assert store.get("missing") is None


@pytest.fixture
def _restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@pytest.mark.usefixtures("_restore_logging")
def test_logging_writes_rotating_file(tmp_path):
    setup_logging(tmp_path, console=True)
    logging.getLogger("t").warning("سلام log")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "سلام log" in (tmp_path / "app.log").read_text(encoding="utf-8")
