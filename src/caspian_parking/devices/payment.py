"""Card terminals / PC-POS (SPEC §4.4, §6, DECISIONS D-013).

``request(amount)`` sends the amount to the terminal and waits for the customer (blocking: the gate runs
it on a worker thread). Iranian PSP terminals (Behpardakht, Sepehr, Pardakht Novin, …) each have their
own SDK/protocol; a driver is added when the PSP's documentation is available. Until then the operator
uses the separate terminal and presses «کارت» (manual mode), exactly as before.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PosResult:
    approved: bool
    trace: str | None = None  # PSP reference / trace number, stored on the payment
    card: str | None = None  # masked card number
    error: str | None = None  # i18n key when not approved: pos.declined | pos.timeout | pos.offline


ERRORS = {"decline": "pos.declined", "timeout": "pos.timeout", "offline": "pos.offline"}


class PaymentTerminal(Protocol):
    name: str

    def request(self, amount: int, timeout_s: int) -> PosResult: ...


class ManualTerminal:
    """No connected terminal: the amount is typed on a stand-alone POS and recorded by the operator."""

    name = "manual"

    def request(self, amount: int, timeout_s: int) -> PosResult:  # pragma: no cover - never called
        return PosResult(True)


class SimulatorTerminal:
    """outcome: approve | decline | timeout | offline."""

    name = "simulator"

    def __init__(self, outcome: str = "approve", delay_s: float = 0.0) -> None:
        self.outcome = outcome
        self.delay_s = delay_s
        self.requests: list[int] = []

    def request(self, amount: int, timeout_s: int) -> PosResult:
        self.requests.append(amount)
        if self.delay_s:
            time.sleep(min(self.delay_s, timeout_s))
        if self.outcome == "approve":
            return PosResult(True, trace=f"{secrets.randbelow(10**12):012d}", card="6037-****-****-1234")
        return PosResult(False, error=ERRORS.get(self.outcome, "pos.declined"))


def create_terminal(kind: str) -> PaymentTerminal | None:
    """None = manual mode (the operator records card payments by hand)."""
    if kind == "simulator":
        return SimulatorTerminal()
    return None
