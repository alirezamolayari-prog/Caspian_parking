"""Receipt data: what to print (content) and how (layout settings). Rendering lives in ``ui.receipt``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from caspian_parking.core.plate import Plate


@dataclass(frozen=True)
class ReceiptAd:
    shop_name: str
    location: str = ""
    offer: str = ""
    logo_path: Path | None = None  # premium packages only
    large: bool = False


@dataclass(frozen=True)
class ReceiptLayout:
    """Receipt settings (paths are resolved by the caller from the data root)."""

    logo_path: Path | None = None
    divider_path: Path | None = None
    barcode_art_path: Path | None = None
    template_path: Path | None = None  # custom header image replacing logo + ad area
    show_logo: bool = True
    show_ad: bool = True
    show_barcode_art: bool = True
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiptContent:
    kind: str  # entry | duplicate | exit
    ticket_no: str
    entry_at: datetime
    vehicle_label: str
    plate: Plate | None = None
    payload: str | None = None
    ad: ReceiptAd | None = None
    training: bool = False
    exit_at: datetime | None = None
    duration_minutes: int | None = None
    amount: int | None = None
    method_label: str | None = None


@dataclass(frozen=True)
class CouponReceipt:
    """One printed coupon: shop header (or the shop's template), code barcode, expiry."""

    code: str
    shop_name: str
    expires_on: date
    template_path: Path | None = None
    training: bool = False
