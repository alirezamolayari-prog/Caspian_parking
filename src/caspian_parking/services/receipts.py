"""Receipt settings → ReceiptLayout, and ReceiptContent builders for gate events."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from caspian_parking.config.paths import DataRoot
from caspian_parking.core.plate import Plate, plate_from_key
from caspian_parking.core.receipt import ReceiptAd, ReceiptContent, ReceiptLayout
from caspian_parking.data.models import ActiveSession
from caspian_parking.i18n import tr
from caspian_parking.services.settings import get_setting, set_setting

LOGO_FILE = "logo.png"
DIVIDER_FILE = "divider.png"

RECEIPT_SETTING_KEYS = (
    "receipt.show_logo",
    "receipt.show_ad",
    "receipt.show_barcode_art",
    "receipt.barcode_art",
    "receipt.divider_image",
    "receipt.labels",
)


def load_layout(session: Session, root: DataRoot, template_path: Path | None = None) -> ReceiptLayout:
    art = get_setting(session, "receipt.barcode_art")
    divider = get_setting(session, "receipt.divider_image")
    return ReceiptLayout(
        logo_path=root.receipt / LOGO_FILE,
        divider_path=(root.receipt / DIVIDER_FILE) if divider else None,
        barcode_art_path=(root.barcode_art / art) if art else None,
        template_path=template_path,
        show_logo=bool(get_setting(session, "receipt.show_logo")),
        show_ad=bool(get_setting(session, "receipt.show_ad")),
        show_barcode_art=bool(get_setting(session, "receipt.show_barcode_art")),
        labels=dict(get_setting(session, "receipt.labels") or {}),
    )


def reset_receipt_settings(session: Session) -> None:
    """'Reset to default receipt' button."""
    from caspian_parking.services.settings import DEFAULTS

    for key in RECEIPT_SETTING_KEYS:
        set_setting(session, key, DEFAULTS[key], reason=tr("receipt.reset"))


def plate_of(active: Any) -> Plate | None:
    key = getattr(active, "plate_key", None)
    if not key:
        return None
    try:
        return plate_from_key(key)
    except ValueError:
        return None


def entry_content(
    active: ActiveSession, payload: str, *, duplicate: bool = False, ad: ReceiptAd | None = None, training: bool = False
) -> ReceiptContent:
    return ReceiptContent(
        kind="duplicate" if duplicate else "entry",
        ticket_no=active.ticket_no,
        entry_at=active.entry_at_utc,
        vehicle_label=tr(f"vehicle.{active.vehicle_type}"),
        plate=plate_of(active),
        payload=payload,
        ad=ad,
        training=training,
    )


def exit_content(
    active: Any,
    exit_at: datetime,
    minutes: int,
    amount: int,
    method: str | None,
    ad: ReceiptAd | None = None,
    training: bool = False,
) -> ReceiptContent:
    return ReceiptContent(
        kind="exit",
        ticket_no=active.ticket_no,
        entry_at=active.entry_at_utc,
        vehicle_label=tr(f"vehicle.{active.vehicle_type}"),
        plate=plate_of(active),
        ad=ad,
        training=training,
        exit_at=exit_at,
        duration_minutes=minutes,
        amount=amount,
        method_label=tr(f"payment.{method}") if method else tr("payment.none"),
    )
