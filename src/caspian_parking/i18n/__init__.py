"""Translation layer. All user-visible strings come from ``fa.json`` via :func:`tr`."""

from __future__ import annotations

import json
import logging
from functools import cache
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "fa"
_language = DEFAULT_LANGUAGE


@cache
def _catalog(language: str) -> dict[str, str]:
    text = resources.files(__package__).joinpath(f"{language}.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def set_language(language: str) -> None:
    global _language
    _catalog(language)  # fail fast if the catalog is missing
    _language = language


def has_key(key: str) -> bool:
    return key in _catalog(_language)


def keys() -> frozenset[str]:
    return frozenset(_catalog(_language))


def tr(key: str, /, **kwargs: object) -> str:
    """Translate ``key``; ``{name}`` placeholders are filled from ``kwargs``.

    A missing key returns the key itself (and logs), so the UI never shows an empty label.
    """
    template = _catalog(_language).get(key)
    if template is None:
        log.warning("missing translation key: %s", key)
        return key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            log.warning("bad placeholders for key %s: %s", key, kwargs)
            return template
    return template
