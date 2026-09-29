"""Installation defaults loaded from ``resources/app_defaults.json`` (no product names in code)."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any


@cache
def app_defaults() -> dict[str, Any]:
    text = resources.files("caspian_parking.resources").joinpath("app_defaults.json").read_text("utf-8")
    return json.loads(text)


@cache
def site_seed() -> dict[str, Any]:
    text = resources.files("caspian_parking.resources").joinpath("seed/site.json").read_text("utf-8")
    return json.loads(text)


def product_name() -> str:
    return str(app_defaults()["product_name"])
