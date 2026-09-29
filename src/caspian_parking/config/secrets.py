"""Secrets store: values are DPAPI-encrypted (machine scope) in ``config\\secrets.json``."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

SECRETS_FILE = "secrets.json"
_CRYPTPROTECT_LOCAL_MACHINE = 0x4
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(
        data, None, None, None, None, _CRYPTPROTECT_LOCAL_MACHINE | _CRYPTPROTECT_UI_FORBIDDEN
    )


def _unprotect(blob: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN)[1]


class SecretStore:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / SECRETS_FILE

    def _read(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, name: str) -> bytes | None:
        blob = self._read().get(name)
        if blob is None:
            return None
        return _unprotect(base64.b64decode(blob))

    def get_text(self, name: str) -> str | None:
        value = self.get(name)
        return value.decode("utf-8") if value is not None else None

    def set(self, name: str, value: bytes | str) -> None:
        raw = value.encode("utf-8") if isinstance(value, str) else value
        data = self._read()
        data[name] = base64.b64encode(_protect(raw)).decode("ascii")
        self._write(data)

    def get_or_create(self, name: str, size: int = 32) -> bytes:
        """Return a random binary secret, generating and storing it on first use."""
        existing = self.get(name)
        if existing is not None:
            return existing
        value = os.urandom(size)
        self.set(name, value)
        return value
