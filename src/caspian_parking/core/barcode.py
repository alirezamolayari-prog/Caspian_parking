"""Ticket barcode: signed numeric payload + Code 128 (set C) encoder (SPEC §2.3).

Payload (20 digits, even → pure Code set C):
    gate(1) + sequence mod 10^6 (6) + entry minute since Unix epoch (8) + HMAC (5)
Any gate can read the entry time offline; an edited or forged ticket fails the HMAC check.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from caspian_parking.core.clock import ensure_utc
from caspian_parking.core.digits import digits_only

PAYLOAD_LENGTH = 20
SEQ_DIGITS = 6
MINUTE_DIGITS = 8
MAC_DIGITS = 5
SEQ_MODULO = 10**SEQ_DIGITS
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class BarcodeError(ValueError):
    pass


class ForgedTicketError(BarcodeError):
    """The payload is well-formed but its signature is wrong (edited or forged ticket)."""


@dataclass(frozen=True)
class TicketPayload:
    gate: int
    sequence_mod: int  # sequence modulo 10^6
    entry_minute: int  # minutes since Unix epoch (UTC)

    @property
    def entry_utc(self) -> datetime:
        return EPOCH + timedelta(minutes=self.entry_minute)


def entry_minute(moment: datetime) -> int:
    return int((ensure_utc(moment) - EPOCH).total_seconds()) // 60


def _mac(key: bytes, body: str) -> str:
    digest = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    return str(int.from_bytes(digest[:8], "big") % 10**MAC_DIGITS).zfill(MAC_DIGITS)


def encode_payload(gate: int, sequence: int, entry: datetime, key: bytes) -> str:
    if not 1 <= gate <= 9:
        raise BarcodeError("gate code must be 1-9")
    if not key:
        raise BarcodeError("HMAC key required")
    body = f"{gate}{sequence % SEQ_MODULO:0{SEQ_DIGITS}d}{entry_minute(entry):0{MINUTE_DIGITS}d}"
    if len(body) != PAYLOAD_LENGTH - MAC_DIGITS:
        raise BarcodeError("entry time out of range")
    return body + _mac(key, body)


def decode_payload(text: str, key: bytes) -> TicketPayload:
    payload = digits_only(text)
    if len(payload) != PAYLOAD_LENGTH:
        raise BarcodeError("wrong payload length")
    body, mac = payload[:-MAC_DIGITS], payload[-MAC_DIGITS:]
    if not hmac.compare_digest(_mac(key, body), mac):
        raise ForgedTicketError("signature mismatch")
    gate = int(body[0])
    if gate == 0:
        raise BarcodeError("gate code must be 1-9")
    return TicketPayload(gate, int(body[1 : 1 + SEQ_DIGITS]), int(body[1 + SEQ_DIGITS :]))


def looks_like_payload(text: str) -> bool:
    cleaned = digits_only(text)
    return len(cleaned) == PAYLOAD_LENGTH and len(text.strip()) <= PAYLOAD_LENGTH + 2


# ---------------------------------------------------------------- Code 128

# Bar/space widths for symbol values 0..105 (6 elements, 11 modules) and STOP (7 elements, 13 modules).
CODE128_PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)  # fmt: skip
START_C = 105
STOP = 106
QUIET_ZONE_MODULES = 10


def code128c_values(digits: str) -> list[int]:
    """Symbol values: START C, digit pairs, checksum, STOP."""
    if not digits or len(digits) % 2 or not digits.isdigit() or not digits.isascii():
        raise BarcodeError("Code set C needs an even number of digits")
    data = [int(digits[i : i + 2]) for i in range(0, len(digits), 2)]
    checksum = (START_C + sum(value * (index + 1) for index, value in enumerate(data))) % 103
    return [START_C, *data, checksum, STOP]


def code128c_modules(digits: str) -> list[bool]:
    """Module sequence (True = bar) without quiet zones."""
    modules: list[bool] = []
    for value in code128c_values(digits):
        bar = True
        for width in CODE128_PATTERNS[value]:
            modules.extend([bar] * int(width))
            bar = not bar
    return modules


def code128_decode_modules(modules: list[bool]) -> str:
    """Reverse of :func:`code128c_modules` (used by tests and the scanner test page)."""
    lookup = {pattern: value for value, pattern in enumerate(CODE128_PATTERNS)}
    runs: list[int] = []
    for index, bar in enumerate(modules):
        if index == 0 or bar != modules[index - 1]:
            runs.append(1)
        else:
            runs[-1] += 1
    widths = "".join(str(r) for r in runs)
    values: list[int] = []
    position = 0
    while position < len(widths):
        chunk = widths[position : position + 7]
        if chunk == CODE128_PATTERNS[STOP]:
            values.append(STOP)
            break
        chunk = widths[position : position + 6]
        if chunk not in lookup:
            raise BarcodeError("unknown symbol")
        values.append(lookup[chunk])
        position += 6
    if len(values) < 3 or values[0] != START_C or values[-1] != STOP:
        raise BarcodeError("missing start/stop")
    data, checksum = values[1:-2], values[-2]
    if (START_C + sum(v * (i + 1) for i, v in enumerate(data))) % 103 != checksum:
        raise BarcodeError("checksum mismatch")
    return "".join(f"{v:02d}" for v in data)
