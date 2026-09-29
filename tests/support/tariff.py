"""Test helpers: tariff values and calendar built from the seed data (SPEC §4.5 defaults)."""

from __future__ import annotations

import dataclasses
from typing import Any

from caspian_parking.config.defaults import site_seed
from caspian_parking.core.tariff import ParkingCalendar, TariffValues


def seed_values(**overrides: Any) -> TariffValues:
    return TariffValues.from_dict({**site_seed()["tariff"], **overrides})


def seed_calendar(**overrides: Any) -> ParkingCalendar:
    data = site_seed()["calendar"]
    base = ParkingCalendar.from_parts(data["hours"], data["free_weekdays"])
    return dataclasses.replace(base, **overrides)
